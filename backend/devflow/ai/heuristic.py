"""Non-AI diagnostic provider: deterministic rules over the classified failures.

Always available. It explains *what* failed and *where*, with source excerpts, but
does not generate patches.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from devflow.ai.base import AnalysisInput, AnalysisOutput, FailureContext
from devflow.runners.base import Availability

_ASSERT_DETAIL = re.compile(r"^E\s+(.*)$", re.M)

_ADVICE = {
    "assertion": "The code under test returned a value the test did not expect. Compare the "
    "expected and actual values below and check the logic at the failing line.",
    "exception": "The code raised an unexpected exception. The deepest repository frame in the "
    "traceback is the most likely place to start.",
    "syntax_error": "Python could not parse a source file, so the tests importing it could not be "
    "collected. Fix the syntax at the reported line.",
    "missing_dependency": "A module imported by the code is not installed in the sandbox. Test "
    "containers have no network access, so dependencies must be part of the runner image or "
    "vendored in the repository. Add the package to the runner image (runner/requirements.txt) "
    "or remove the import.",
    "timeout": "A test did not finish within the time limit. Look for infinite loops, blocking "
    "I/O, or calls that wait on the network (which is disabled inside the sandbox).",
    "collection_error": "pytest failed while importing test modules. Check the import error and "
    "module-level code in the reported file.",
    "infrastructure": "The run failed for reasons outside the test code (runner or pytest usage "
    "error). Check the run's stderr log.",
}


class HeuristicProvider:
    name = "heuristic"
    label = "Rule-based diagnostics (no AI)"
    is_ai = False
    generates_patches = False

    def availability(self) -> Availability:
        return Availability(True, "built in")

    def analyze(self, request: AnalysisInput, workspace: Path) -> AnalysisOutput:
        if not request.failures:
            return AnalysisOutput(
                "No failures recorded for this run", "Nothing to diagnose.", "high"
            )
        categories = Counter(f.category for f in request.failures)
        dominant, count = categories.most_common(1)[0]
        root_cause = (
            f"{count} of {len(request.failures)} failure(s) are '{dominant}': "
            f"{next(f for f in request.failures if f.category == dominant).summary}"
        )
        parts = [_ADVICE.get(dominant, _ADVICE["exception"])]
        for failure in request.failures:
            parts.append(_describe(failure, request.files))
        confidence = "medium" if dominant in ("syntax_error", "missing_dependency") else "low"
        return AnalysisOutput(root_cause[:2000], "\n\n".join(parts)[:8000], confidence)


def _describe(failure: FailureContext, files: dict[str, str]) -> str:
    lines = [f"- {failure.nodeid or 'run'} [{failure.category}] {failure.summary}"]
    details = _ASSERT_DETAIL.findall(failure.traceback or "")[:4]
    if details:
        lines.append("  Details: " + " | ".join(d.strip() for d in details))
    if failure.file and failure.line:
        lines.append(f"  Location: {failure.file}:{failure.line}")
        excerpt = _excerpt(files.get(failure.file), failure.line)
        if excerpt:
            lines.append(excerpt)
    return "\n".join(lines)


def _excerpt(content: str | None, line: int, radius: int = 3) -> str | None:
    if not content:
        return None
    source = content.splitlines()
    start, end = max(1, line - radius), min(len(source), line + radius)
    return "\n".join(
        f"  {'>' if n == line else ' '} {n:4d} | {source[n - 1]}" for n in range(start, end + 1)
    )

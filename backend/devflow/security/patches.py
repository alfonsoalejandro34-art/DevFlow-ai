"""Static validation and risk scoring of proposed unified diffs.

Every patch, whether from an AI provider or a scripted demo, is treated as untrusted:
it must be a plain-text unified diff that only edits regular files inside the
repository. Anything else (binary data, symlinks, renames, git metadata, absolute or
traversing paths) is rejected before ``git apply`` ever sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from devflow.errors import SecurityViolation
from devflow.security.paths import safe_relative_path

MAX_PATCH_BYTES = 200_000
MAX_FILES = 20

_FORBIDDEN_MARKERS = (
    ("GIT binary patch", "binary patches are not allowed"),
    ("Binary files ", "binary patches are not allowed"),
    ("new mode ", "file mode changes are not allowed"),
    ("old mode ", "file mode changes are not allowed"),
    ("rename from ", "renames are not allowed"),
    ("copy from ", "copies are not allowed"),
    ("similarity index", "renames/copies are not allowed"),
)
_HEADER_PATH = re.compile(r"^(?:---|\+\+\+) (?:[ab]/)?(?P<path>[^\t\n]+)")
_SENSITIVE_FILES = re.compile(
    r"(^|/)(setup\.py|setup\.cfg|pyproject\.toml|conftest\.py|requirements[^/]*\.txt|"
    r"Makefile|Dockerfile|tox\.ini|noxfile\.py|\.github/.*|\.pre-commit-config\.yaml)$"
)
_TEST_FILES = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$)")
_SUSPICIOUS_ADDED = re.compile(
    r"\b(subprocess|os\.system|os\.popen|eval\(|exec\(|__import__|socket|urllib|requests\.|"
    r"httpx|shutil\.rmtree|ctypes|pickle\.loads|base64\.b64decode)"
)
_TEST_WEAKENING = re.compile(
    r"^\+.*(pytest\.mark\.skip|pytest\.skip\(|@unittest\.skip|xfail|assert True\b)"
)


@dataclass
class PatchReport:
    files: list[str] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    deleted_files: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    risk_score: int = 0
    risk_level: str = "low"
    risk_reasons: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def normalize_diff(diff: str) -> str:
    """Fix formatting slips common in model-written diffs without changing content:
    CRLF line endings, a missing final newline, and file headers emitted with a stray
    leading space (`` diff --git ...``), which git would otherwise read as context."""
    text = diff.replace("\r\n", "\n")
    text = re.sub(r"^ (diff --git a/\S+ b/\S+)$", r"\1", text, flags=re.M)
    return text if text.endswith("\n") else text + "\n"


def validate_patch(diff: str) -> PatchReport:
    report = PatchReport()
    diff = normalize_diff(diff)
    if not diff.strip():
        report.errors.append("patch is empty")
        return report
    if len(diff.encode()) > MAX_PATCH_BYTES:
        report.errors.append(f"patch exceeds {MAX_PATCH_BYTES} bytes")
        return report
    if "\x00" in diff:
        report.errors.append("patch contains NUL bytes")
        return report

    files: list[str] = []
    saw_hunk = False
    for line in diff.splitlines():
        for marker, reason in _FORBIDDEN_MARKERS:
            if line.startswith(marker) and reason not in report.errors:
                report.errors.append(reason)
        if line.startswith(("new file mode", "deleted file mode")) and "120000" in line:
            report.errors.append("symbolic links are not allowed")
        if line.startswith("@@"):
            saw_hunk = True
        if line.startswith("diff --git "):
            for token in line[len("diff --git ") :].split():
                try:
                    safe_relative_path(re.sub(r"^[ab]/", "", token))
                except SecurityViolation as exc:
                    report.errors.append(exc.message)
        match = _HEADER_PATH.match(line)
        if match and not line.startswith(("--- ", "+++ ")):
            match = None
        if match:
            raw = match.group("path").strip()
            if raw == "/dev/null":
                continue
            try:
                safe_relative_path(raw)
            except SecurityViolation as exc:
                report.errors.append(exc.message)
                continue
            if raw not in files:
                files.append(raw)
        elif line.startswith("+") and not line.startswith("+++"):
            report.added += 1
        elif line.startswith("-") and not line.startswith("---"):
            report.removed += 1

    if not files or not saw_hunk:
        report.errors.append("not a unified diff (missing file headers or hunks)")
    if len(files) > MAX_FILES:
        report.errors.append(f"patch touches more than {MAX_FILES} files")
    report.files = files
    report.deleted_files = _paths_after(diff, "+++ /dev/null", "--- ")
    report.created_files = _paths_after(diff, "--- /dev/null", "+++ ", lookahead=True)
    _score(diff, report)
    return report


def _paths_after(diff: str, marker: str, prefix: str, *, lookahead: bool = False) -> list[str]:
    lines = diff.splitlines()
    found = []
    for i, line in enumerate(lines):
        if line.strip() != marker:
            continue
        j = i + 1 if lookahead else i - 1
        if 0 <= j < len(lines) and lines[j].startswith(prefix):
            found.append(re.sub(r"^(?:---|\+\+\+) (?:[ab]/)?", "", lines[j]).strip())
    return found


def _score(diff: str, report: PatchReport) -> None:
    score = 0
    reasons: list[str] = []
    changed = report.added + report.removed
    if changed > 10:
        score += min(30, changed // 5)
        reasons.append(f"{changed} lines changed")
    if len(report.files) > 2:
        score += 10 * (len(report.files) - 2)
        reasons.append(f"touches {len(report.files)} files")
    test_files = [f for f in report.files if _TEST_FILES.search(f)]
    if test_files:
        score += 25
        reasons.append(
            "modifies test files, which can mask a real failure: " + ", ".join(test_files)
        )
    if any(_TEST_WEAKENING.match(line) for line in diff.splitlines()):
        score += 30
        reasons.append("adds skip/xfail markers or trivially true assertions")
    sensitive = [f for f in report.files if _SENSITIVE_FILES.search(f)]
    if sensitive:
        score += 25
        reasons.append("modifies build/configuration files: " + ", ".join(sensitive))
    if report.deleted_files:
        score += 20
        reasons.append("deletes files: " + ", ".join(report.deleted_files))
    added_lines = [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    if any(_SUSPICIOUS_ADDED.search(line) for line in added_lines):
        score += 35
        reasons.append("adds process, network, deserialization, or filesystem-removal calls")
    if not reasons:
        reasons.append("small, localized change to non-test source")
    report.risk_score = min(score, 100)
    report.risk_level = "low" if score < 25 else "medium" if score < 60 else "high"
    report.risk_reasons = reasons

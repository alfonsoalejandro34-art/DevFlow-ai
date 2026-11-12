"""Deterministic failure classification and signatures for repeated-failure tracking."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from devflow.database.models import FailureCategory

_MISSING_MODULE = re.compile(r"No module named '([^']+)'")
_EXCEPTION_LINE = re.compile(r"^E?\s*([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt))\b", re.M)
_LOCATION = re.compile(r"^(?P<file>[^\s:][^:\n]*\.py):(?P<line>\d+):", re.M)
_GENERIC_MESSAGES = {"collection failure", "failed on setup with", "failed on teardown with"}
_VOLATILE = re.compile(r"0x[0-9a-f]+|\b\d+(?:\.\d+)?\b")


@dataclass
class Classification:
    category: FailureCategory
    exception_type: str | None
    summary: str
    file: str | None
    line: int | None


def classify(
    outcome: str, message: str | None, traceback: str | None, nodeid: str
) -> Classification:
    text = "\n".join(p for p in (message, traceback) if p)
    exception_type = _exception_type(text)
    category = _category(outcome, text, exception_type, nodeid)
    file, line = _location(traceback or "")
    summary = _summary(category, message, text, exception_type)
    return Classification(category, exception_type, summary, file, line)


def classify_run_error(stdout: str, stderr: str, exit_code: int | None) -> Classification:
    """Classify a run that produced no per-test results (e.g. pytest usage error)."""
    text = f"{stdout}\n{stderr}"
    exception_type = _exception_type(text)
    category = _category("error", text, exception_type, "<collection>")
    if category == FailureCategory.EXCEPTION and exit_code in (3, 4):
        category = FailureCategory.INFRASTRUCTURE
    file, line = _location(text)
    tail = [ln for ln in text.strip().splitlines() if ln.strip()][-1:] or ["no output"]
    return Classification(category, exception_type, tail[0][:500], file, line)


def signature(category: str, nodeid: str | None, exception_type: str | None, summary: str) -> str:
    normalized = _VOLATILE.sub("#", summary)[:300]
    raw = "|".join((category, nodeid or "", exception_type or "", normalized))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _exception_type(text: str) -> str | None:
    matches = _EXCEPTION_LINE.findall(text)
    return matches[-1] if matches else None


def _category(outcome: str, text: str, exception_type: str | None, nodeid: str) -> FailureCategory:
    if "Timeout" in text and (
        "pytest-timeout" in text or "Failed: Timeout" in text or "+++ Timeout" in text
    ):
        return FailureCategory.TIMEOUT
    if exception_type in ("SyntaxError", "IndentationError", "TabError"):
        return FailureCategory.SYNTAX_ERROR
    if exception_type in ("ModuleNotFoundError", "ImportError") and _MISSING_MODULE.search(text):
        return FailureCategory.MISSING_DEPENDENCY
    if outcome == "error" and (
        "collection failure" in text or "ERROR collecting" in text or nodeid == "<collection>"
    ):
        return FailureCategory.COLLECTION_ERROR
    # A concrete exception type wins over "assert" appearing in echoed test source.
    if exception_type == "AssertionError" or (
        outcome == "failed" and exception_type is None and ("assert " in text or "Failed:" in text)
    ):
        return FailureCategory.ASSERTION
    return FailureCategory.EXCEPTION


def _location(traceback: str) -> tuple[str | None, int | None]:
    candidates = [
        m
        for m in _LOCATION.finditer(traceback)
        if "site-packages" not in m.group("file") and not m.group("file").startswith("/usr/")
    ]
    if not candidates:
        return None, None
    last = candidates[-1]
    file = last.group("file")
    for prefix in ("/src/", "./"):
        file = file.removeprefix(prefix)
    return file, int(last.group("line"))


def _summary(
    category: FailureCategory, message: str | None, text: str, exception_type: str | None
) -> str:
    if category == FailureCategory.MISSING_DEPENDENCY:
        match = _MISSING_MODULE.search(text)
        return (
            f"Missing dependency: module '{match.group(1)}' is not installed"
            if match
            else "Missing dependency"
        )
    if message and message.strip() not in _GENERIC_MESSAGES:
        return message.strip().splitlines()[0][:500]
    for line in reversed(text.strip().splitlines()):
        if line.startswith("E ") or (exception_type and exception_type in line):
            return line.lstrip("E ").strip()[:500]
    return (exception_type or category.value)[:500]

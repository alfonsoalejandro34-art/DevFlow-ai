"""Parse pytest JUnit XML output. The XML is produced inside the sandbox and is
therefore untrusted: it is size-capped and parsed with defusedxml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from defusedxml import ElementTree

from devflow.security.redact import redact

MAX_JUNIT_BYTES = 20 * 1024 * 1024
MAX_TRACEBACK_CHARS = 20_000


@dataclass
class ParsedTest:
    nodeid: str
    file: str | None
    name: str
    outcome: str  # passed | failed | error | skipped
    duration_ms: float
    message: str | None
    traceback: str | None


class JUnitParseError(Exception):
    pass


def parse_junit(path: Path) -> list[ParsedTest]:
    if not path.is_file():
        raise JUnitParseError("JUnit report was not produced")
    if path.stat().st_size > MAX_JUNIT_BYTES:
        raise JUnitParseError("JUnit report is too large")
    try:
        root = ElementTree.parse(str(path)).getroot()
        if root is None:
            raise JUnitParseError("Empty JUnit report")
    except JUnitParseError:
        raise
    except Exception as exc:  # defusedxml raises several exception types
        raise JUnitParseError(f"Malformed JUnit report: {exc}") from exc

    tests: list[ParsedTest] = []
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        file = case.get("file") or _file_from_classname(classname)
        outcome, message, traceback = "passed", None, None
        for child_tag, child_outcome in (
            ("failure", "failed"),
            ("error", "error"),
            ("skipped", "skipped"),
        ):
            child = case.find(child_tag)
            if child is not None:
                outcome = child_outcome
                message = child.get("message")
                traceback = (child.text or "").strip() or None
                break
        tests.append(
            ParsedTest(
                nodeid=_nodeid(classname, name, file),
                file=file,
                name=name,
                outcome=outcome,
                duration_ms=round(float(case.get("time") or 0) * 1000, 2),
                message=redact(message)[:2000] if message else None,
                traceback=redact(traceback)[:MAX_TRACEBACK_CHARS] if traceback else None,
            )
        )
    return tests


def suite_duration_ms(path: Path) -> int | None:
    try:
        root = ElementTree.parse(str(path)).getroot()
    except Exception:
        return None
    if root is None:
        return None
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None or suite.get("time") is None:
        return None
    return int(float(suite.get("time", "0")) * 1000)


def _file_from_classname(classname: str) -> str | None:
    if not classname:
        return None
    parts = classname.split(".")
    # "tests.test_pricing.TestDiscount" -> tests/test_pricing.py
    module_parts = [p for p in parts if not p[:1].isupper()] or parts
    return "/".join(module_parts) + ".py"


def _nodeid(classname: str, name: str, file: str | None) -> str:
    if not classname:
        return name or "<collection>"
    if file:
        module = file[:-3].replace("/", ".") if file.endswith(".py") else file
        rest = classname[len(module) :].lstrip(".") if classname.startswith(module) else ""
        return "::".join(p for p in (file, rest.replace(".", "::"), name) if p)
    return f"{classname}::{name}"

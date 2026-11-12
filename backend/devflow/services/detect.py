"""Language and project-structure detection from a Git tree listing (file names only;
no repository file is read or executed)."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from typing import Any

_EXTENSIONS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".hpp": "C++",
    ".rb": "Ruby",
    ".sh": "Shell",
    ".cs": "C#",
    ".swift": "Swift",
    ".lua": "Lua",
    ".luau": "Luau",
}
_MARKERS = {
    "pyproject.toml": "Python project (pyproject.toml)",
    "setup.py": "Python package (setup.py)",
    "requirements.txt": "pip requirements",
    "package.json": "Node.js package",
    "go.mod": "Go module",
    "Cargo.toml": "Rust crate",
    "CMakeLists.txt": "CMake project",
    "Dockerfile": "Dockerfile",
    "pytest.ini": "pytest configuration",
    "conftest.py": "pytest conftest",
    "tox.ini": "tox configuration",
}


def detect_structure(paths: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(languages, structure)`` for a list of repository-relative paths."""
    counts: Counter[str] = Counter()
    markers: list[str] = []
    test_files: list[str] = []
    top_level: set[str] = set()
    for raw in paths:
        path = PurePosixPath(raw)
        top_level.add(path.parts[0] + ("/" if len(path.parts) > 1 else ""))
        language = _EXTENSIONS.get(path.suffix.lower())
        if language:
            counts[language] += 1
        if path.name in _MARKERS and len(path.parts) <= 2:
            markers.append(_MARKERS[path.name])
        if path.suffix == ".py" and (
            path.name.startswith("test_") or path.name.endswith("_test.py")
        ):
            test_files.append(raw)

    total = sum(counts.values()) or 1
    languages = {lang: round(100 * n / total, 1) for lang, n in counts.most_common()}
    has_pytest = bool(test_files)
    structure: dict[str, Any] = {
        "file_count": len(paths),
        "top_level": sorted(top_level)[:50],
        "markers": sorted(set(markers)),
        "test_framework": "pytest" if has_pytest else None,
        "test_file_count": len(test_files),
        "supported": has_pytest,
        "unsupported_reason": None if has_pytest else "No pytest test files (test_*.py) found",
    }
    return languages, structure

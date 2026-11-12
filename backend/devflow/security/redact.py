"""Secret redaction for logs, stored test output, and AI prompts."""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
        REDACTED,
    ),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}"), REDACTED),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), REDACTED),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-~+/]{16,}=*"), r"\1 " + REDACTED),
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)"
            r"[A-Za-z0-9_]*)(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}\3"
        ),
        r"\1\2\3" + REDACTED + r"\3",
    ),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


SECRET_ENV_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
)
SECRET_ENV_PREFIXES = ("AWS_", "GITHUB_", "GH_", "AZURE_", "GOOGLE_", "GCP_", "SSH_", "NPM_")


def is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    return any(m in upper for m in SECRET_ENV_MARKERS) or upper.startswith(SECRET_ENV_PREFIXES)

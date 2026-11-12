"""Local API token. Generated on first start and stored with 0600 permissions; every
API route except /api/health requires it as a Bearer token."""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

TOKEN_FILENAME = "api_token"  # noqa: S105 - a file name, not a credential


def load_or_create_token(data_dir: Path, configured: str | None = None) -> str:
    if configured:
        return configured
    path = data_dir / TOKEN_FILENAME
    if path.exists():
        token = path.read_text().strip()
        if token:
            return token
    data_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(token + "\n")
    return token


def token_matches(expected: str, authorization: str | None) -> bool:
    if not authorization:
        return False
    scheme, _, supplied = authorization.partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        return False
    return hmac.compare_digest(expected.encode(), supplied.strip().encode())

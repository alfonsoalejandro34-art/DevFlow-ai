"""Applying validated patches to DevFlow-owned directories (never to user repositories)."""

from __future__ import annotations

from pathlib import Path

from devflow.errors import CommandFailed, ValidationFailed
from devflow.security.commands import minimal_env, run_command
from devflow.security.patches import normalize_diff, validate_patch


def apply_patch(dest: Path, diff: str, *, check_only: bool = False) -> None:
    """Apply ``diff`` to the plain directory ``dest`` using ``git apply``.

    ``GIT_CEILING_DIRECTORIES`` stops git from discovering an enclosing repository, so
    ``git apply`` acts as a plain patch tool confined to ``dest`` and refuses paths
    that would leave it.
    """
    report = validate_patch(diff)
    if not report.valid:
        raise ValidationFailed("Patch rejected: " + "; ".join(report.errors))
    env = minimal_env(
        {
            "GIT_CEILING_DIRECTORIES": str(dest.resolve().parent),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "HOME": str(dest.resolve().parent),
        }
    )
    args = ["apply", "--recount", "--whitespace=nowarn", "-p1"]
    if check_only:
        args.append("--check")
    try:
        run_command(
            "git", [*args, "-"], cwd=dest, env=env, input_bytes=normalize_diff(diff).encode()
        )
    except CommandFailed as exc:
        raise ValidationFailed(f"Patch does not apply cleanly: {exc.message}") from exc

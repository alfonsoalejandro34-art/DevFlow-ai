"""Claude Code and Codex CLI providers.

Both run non-interactively in a throwaway workspace directory that contains only the
context files, with the prompt on stdin and a scrubbed environment. Neither is ever
given an approval-bypass flag.

* Claude Code: ``claude -p --tools ""`` disables every tool, so the model can only
  return text. MCP servers are excluded with ``--strict-mcp-config``.
* Codex: ``codex exec --sandbox read-only``. Codex's sandbox blocks writes and network
  access, but it does not stop shell commands from *reading* files the user can read.
  See docs/SECURITY.md (residual risks).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from devflow.ai.base import AnalysisInput, AnalysisOutput, ProviderError
from devflow.ai.context import build_prompt, parse_response
from devflow.errors import CommandFailed
from devflow.runners.base import Availability
from devflow.security.commands import minimal_env, run_command

_AUTH_CACHE_SECONDS = 60
_API_KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY")


def ai_env(pass_api_keys: bool) -> dict[str, str]:
    # HOME/USER are needed for the CLIs to find their own stored login.
    extra = {k: os.environ[k] for k in ("USER", "LOGNAME", "CODEX_HOME") if k in os.environ}
    if pass_api_keys:
        extra |= {k: os.environ[k] for k in _API_KEY_VARS if k in os.environ}
    return minimal_env(extra, keep_home=True)


class _CliProvider:
    name = ""
    label = ""
    logical = ""
    is_ai = True
    generates_patches = True

    def __init__(self, binary: str, timeout: int, pass_api_keys: bool) -> None:
        self.binary = binary
        self.timeout = timeout
        self.pass_api_keys = pass_api_keys
        self._cached: tuple[float, Availability] | None = None

    def availability(self) -> Availability:
        now = time.monotonic()
        if self._cached and now - self._cached[0] < _AUTH_CACHE_SECONDS:
            return self._cached[1]
        result = self._check()
        self._cached = (now, result)
        return result

    def _check(self) -> Availability:
        if shutil.which(self.binary) is None:
            return Availability(False, f"{self.binary!r} is not installed")
        try:
            version = run_command(
                self.logical,
                ["--version"],
                configured=self.binary,
                env=ai_env(self.pass_api_keys),
                timeout=20,
            )
        except CommandFailed as exc:
            return Availability(False, exc.message)
        ok, reason = self._authenticated()
        return Availability(ok, reason, version.stdout.decode().strip()[:80])

    def _authenticated(self) -> tuple[bool, str | None]:
        raise NotImplementedError

    def _run(self, args: list[str], prompt: str, workspace: Path) -> bytes:
        try:
            result = run_command(
                self.logical,
                args,
                configured=self.binary,
                cwd=workspace,
                env=ai_env(self.pass_api_keys),
                input_bytes=prompt.encode(),
                timeout=self.timeout,
            )
        except CommandFailed as exc:
            raise ProviderError(exc.message) from exc
        return result.stdout


class ClaudeCodeProvider(_CliProvider):
    name = "claude-code"
    label = "Claude Code CLI"
    logical = "claude"

    def _authenticated(self) -> tuple[bool, str | None]:
        try:
            out = run_command(
                "claude",
                ["auth", "status"],
                configured=self.binary,
                env=ai_env(self.pass_api_keys),
                timeout=20,
                check=False,
            )
            status = json.loads(out.stdout or b"{}")
        except (CommandFailed, json.JSONDecodeError):
            return False, "could not determine Claude Code login status"
        if not status.get("loggedIn"):
            return False, "Claude Code is not logged in (run `claude auth login`)"
        return True, f"logged in via {status.get('authMethod', 'unknown')}"

    def analyze(self, request: AnalysisInput, workspace: Path) -> AnalysisOutput:
        args = [
            "-p",
            "--output-format", "json",
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--permission-mode", "default",
        ]  # fmt: skip
        stdout = self._run(args, build_prompt(request), workspace)
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError("Claude Code returned non-JSON output") from exc
        if envelope.get("is_error"):
            raise ProviderError(
                f"Claude Code reported an error: {str(envelope.get('result'))[:300]}"
            )
        return parse_response(str(envelope.get("result", "")))


class CodexProvider(_CliProvider):
    name = "codex"
    label = "OpenAI Codex CLI"
    logical = "codex"

    def _authenticated(self) -> tuple[bool, str | None]:
        try:
            out = run_command(
                "codex",
                ["login", "status"],
                configured=self.binary,
                env=ai_env(self.pass_api_keys),
                timeout=20,
                check=False,
            )
        except CommandFailed:
            return False, "could not determine Codex login status"
        text = (out.stdout + out.stderr).decode(errors="replace").strip()
        if out.returncode != 0 or "not logged in" in text.lower():
            return False, "Codex is not logged in (run `codex login`)"
        return True, text.splitlines()[0][:80] if text else None

    def analyze(self, request: AnalysisInput, workspace: Path) -> AnalysisOutput:
        last_message = workspace / "last_message.txt"
        args = [
            "exec",
            "--sandbox", "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--color", "never",
            "-C", str(workspace),
            "-o", str(last_message),
            "-",
        ]  # fmt: skip
        self._run(args, build_prompt(request), workspace)
        if not last_message.is_file():
            raise ProviderError("Codex did not produce a final message")
        return parse_response(last_message.read_text(errors="replace"))

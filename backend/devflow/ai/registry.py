"""Constructs the configured providers."""

from __future__ import annotations

from devflow.ai.base import Provider
from devflow.ai.cli_providers import ClaudeCodeProvider, CodexProvider
from devflow.ai.demo import DemoProvider
from devflow.ai.heuristic import HeuristicProvider
from devflow.config import Settings


def build_providers(settings: Settings) -> dict[str, Provider]:
    providers: list[Provider] = [
        HeuristicProvider(),
        ClaudeCodeProvider(
            settings.claude_binary, settings.ai_timeout_seconds, settings.ai_pass_api_keys
        ),
        CodexProvider(
            settings.codex_binary, settings.ai_timeout_seconds, settings.ai_pass_api_keys
        ),
        DemoProvider(settings.examples_dir / "demo-fixes"),
    ]
    return {p.name: p for p in providers}

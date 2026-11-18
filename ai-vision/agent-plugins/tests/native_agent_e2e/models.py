"""Shared boundary between the native runtime drivers and acceptance runner."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CaseContext:
    repo: Path
    out: Path
    workspace: Path
    runtime: tuple[str, ...]
    provider_url: str
    collector_url: str
    scenario: str
    prompt: str
    app: str = ""
    runtime_version: str = ""
    followup_prompt: str = ""
    mode: str = "fake"
    model: str = "native-e2e-chat"
    headers: dict[str, str] = field(default_factory=dict)
    resource: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 120


@dataclass(frozen=True)
class AgentResult:
    exit_code: int
    session_id: str | None
    version: str
    output: str
    details: dict[str, Any] = field(default_factory=dict)

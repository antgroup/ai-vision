"""Explicit state registries for the canonical Hermes semantic engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApiRequestState:
    key: str
    request_id: str
    span_key: str
    turn_key: str
    model: str
    provider: str
    input_messages: Any
    system_instructions: Any
    started_at: str
    start_unix_nano: int
    terminal: bool = False


@dataclass
class ToolCallState:
    key: str
    call_id: str
    span_key: str
    turn_key: str
    name: str
    tool_type: str
    synthetic: bool
    arguments: Any
    arguments_present: bool
    started_at: str
    start_unix_nano: int
    terminal: bool = False


@dataclass
class SubagentState:
    key: str
    span_key: str
    turn_key: str
    child_session_id: str
    child_agent_id: str
    child_role: str
    started_at: str
    start_unix_nano: int
    terminal: bool = False


@dataclass
class TurnState:
    key: str
    source_id: str
    span_key: str
    number: int
    started_at: str
    start_unix_nano: int
    user_message: Any = None
    assistant_response: Any = None
    terminal: bool = False
    api_requests: dict[str, ApiRequestState] = field(default_factory=dict)
    tools: dict[str, ToolCallState] = field(default_factory=dict)
    subagents: dict[str, SubagentState] = field(default_factory=dict)


@dataclass
class SessionState:
    session_id: str
    model: str = ""
    platform: str = ""
    turn_counter: int = 0
    active_turn: TurnState | None = None
    terminal: bool = False
    session_end_emitted: bool = False
    start_event: Any = None
    session_start_emitted: bool = False
    first_turn_id: str | None = None
    first_turn_key: str | None = None
    last_turn_id: str | None = None
    last_turn_key: str | None = None


@dataclass
class StateRegistry:
    sessions: dict[str, SessionState] = field(default_factory=dict)

    def get_or_create_session(
        self, session_id: str, *, model: str = "", platform: str = ""
    ) -> SessionState:
        session = self.sessions.get(session_id)
        if session is None:
            session = SessionState(
                session_id=session_id, model=model, platform=platform
            )
            self.sessions[session_id] = session
        else:
            if model:
                session.model = model
            if platform:
                session.platform = platform
        return session

    def pending_counts(self) -> dict[str, int]:
        turns = [
            session.active_turn
            for session in self.sessions.values()
            if session.active_turn
        ]
        return {
            "sessions": len(self.sessions),
            "turns": len(turns),
            "api_requests": sum(len(turn.api_requests) for turn in turns),
            "tools": sum(len(turn.tools) for turn in turns),
            "subagents": sum(len(turn.subagents) for turn in turns),
        }

    def clear(self) -> None:
        self.sessions.clear()

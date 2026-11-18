"""Single canonical-event semantic reducer for live and replay execution."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .canonical import CanonicalEvent
from .normalize import classify_error
from .privacy import canonical_json
from .state import (
    ApiRequestState,
    SessionState,
    StateRegistry,
    SubagentState,
    ToolCallState,
    TurnState,
)

ERROR_CATEGORIES = {
    "api_error",
    "network_error",
    "timeout",
    "rate_limit",
    "auth",
    "permission",
    "validation",
    "tool_error",
    "cancelled",
    "other",
}


@dataclass(frozen=True)
class SemanticConfig:
    username: str = "unknown"
    user_id: str = "unknown"
    agent_type: str = "hermes"
    agent_product: str = "hermes"
    runtime_name: str = "hermes"
    collection_mode: str = "full"


@dataclass(frozen=True)
class SemanticObservation:
    type: str
    name: str
    timestamp: str
    attributes: dict[str, Any] = field(default_factory=dict)
    key: str | None = None
    parent_key: str | None = None
    span_kind: str | None = None
    metric_kind: str | None = None
    unit: str | None = None
    value: float | int | None = None
    status: str | None = None


class MemorySink:
    def __init__(self) -> None:
        self.observations: list[SemanticObservation] = []

    def __call__(self, observation: SemanticObservation) -> None:
        self.observations.append(observation)

    def of_type(self, kind: str) -> list[SemanticObservation]:
        return [item for item in self.observations if item.type == kind]


class SemanticEngine:
    """Apply canonical facts exactly once and emit transport-neutral semantics."""

    def __init__(
        self,
        config: SemanticConfig | None = None,
        sink: Callable[[SemanticObservation], None] | None = None,
        *,
        turn_number_allocator: Callable[[str, str], int] | None = None,
    ) -> None:
        self.config = config or SemanticConfig()
        self.sink = sink or (lambda _observation: None)
        self.state = StateRegistry()
        self._turn_number_allocator = turn_number_allocator
        self._seen_event_ids: set[str] = set()
        self._terminal_sessions: set[str] = set()
        self._event_sequences: dict[str, int] = {}
        # Hermes 0.21+ may finish a title request after session finalization.
        # Retain only bounded parent identity, independently of live turn state.
        self._auxiliary_turns: OrderedDict[tuple[str, str], TurnState] = OrderedDict()
        self._auxiliary_turn_limit = 512

    def apply(self, event: CanonicalEvent) -> None:
        if event.event_id in self._seen_event_ids:
            self._diagnostic(event, "duplicate_canonical_event", event.event_id)
            return
        self._seen_event_ids.add(event.event_id)
        handler = getattr(self, f"_on_{event.hook}", None)
        if handler is None:
            self._diagnostic(event, "unsupported_canonical_hook", event.hook)
            return
        handler(event)

    def shutdown(self, timestamp: str | None = None) -> None:
        """Discard incomplete state without inventing non-canonical telemetry.

        Canonical lifecycle events are the only authority allowed to terminate
        spans. Process teardown may abandon pending work, but it must not
        synthesize a timestamp or an unpersisted session-finalize event.
        """
        del timestamp
        self.state.clear()
        self._auxiliary_turns.clear()

    def _emit(self, observation: SemanticObservation) -> None:
        self.sink(observation)

    def _span_start(
        self,
        event: CanonicalEvent,
        *,
        key: str,
        parent: str | None,
        name: str,
        kind: str,
        attributes: dict[str, Any],
    ) -> None:
        self._emit(
            SemanticObservation(
                type="span.start",
                name=name,
                key=key,
                parent_key=parent,
                span_kind=kind,
                timestamp=event.timestamp,
                attributes=attributes,
            )
        )

    def _span_end(
        self,
        timestamp: str,
        *,
        key: str,
        name: str,
        attributes: dict[str, Any],
        status: str,
    ) -> None:
        self._emit(
            SemanticObservation(
                type="span.end",
                name=name,
                key=key,
                timestamp=timestamp,
                status=status,
                attributes=attributes,
            )
        )

    def _event(
        self,
        event: CanonicalEvent,
        *,
        name: str,
        owner: str | None,
        operation: str,
        turn: TurnState | None,
        attributes: dict[str, Any] | None = None,
        turn_id: str | None = None,
        turn_number: int | None = None,
    ) -> None:
        sequence = self._event_sequences.get(event.session_id, 0) + 1
        self._event_sequences[event.session_id] = sequence
        common = self._event_common(
            event.session_id,
            name,
            operation,
            sequence,
            turn,
            turn_id=turn_id,
            turn_number=turn_number,
        )
        self._emit(
            SemanticObservation(
                type="event",
                name=name,
                key=owner,
                timestamp=event.timestamp,
                attributes={**common, **(attributes or {})},
            )
        )

    def _metric(
        self,
        event: CanonicalEvent,
        *,
        name: str,
        value: float | int,
        metric_kind: str,
        unit: str,
        attributes: dict[str, str],
    ) -> None:
        self._emit(
            SemanticObservation(
                type="metric",
                name=name,
                timestamp=event.timestamp,
                value=value,
                metric_kind=metric_kind,
                unit=unit,
                attributes=attributes,
            )
        )

    def _diagnostic(self, event: CanonicalEvent, code: str, detail: str = "") -> None:
        self._emit(
            SemanticObservation(
                type="diagnostic",
                name=code,
                timestamp=event.timestamp,
                attributes={
                    "code": code,
                    **({"detail": detail} if detail else {}),
                    "sequence": event.sequence,
                },
            )
        )

    def _span_common(
        self, session_id: str, kind: str, operation: str, turn: TurnState | None
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "user.id": self.config.user_id,
            "ant.username": self.config.username,
            "gen_ai.conversation.id": session_id,
            "gen_ai.operation.name": operation,
            "gen_ai.span.kind": kind,
            "agentic.runtime.name": self.config.runtime_name,
        }
        if turn is not None:
            attributes.update(
                {
                    "gen_ai.chat.id": turn.source_id,
                    "agentic.turn.id": turn.source_id,
                    "agentic.turn.number": turn.number,
                }
            )
        return attributes

    def _event_common(
        self,
        session_id: str,
        name: str,
        operation: str,
        sequence: int,
        turn: TurnState | None,
        *,
        turn_id: str | None = None,
        turn_number: int | None = None,
    ) -> dict[str, Any]:
        resolved_turn_id = turn.source_id if turn is not None else turn_id
        resolved_turn_number = turn.number if turn is not None else turn_number
        attributes = {
            "event.name": name,
            "gen_ai.operation.name": operation,
            "ant.username": self.config.username,
            "gen_ai.conversation.id": session_id,
            "agentic.runtime.name": self.config.runtime_name,
            "event.sequence": sequence,
        }
        if resolved_turn_id:
            attributes.update(
                {
                    "gen_ai.chat.id": resolved_turn_id,
                    "agentic.turn.id": resolved_turn_id,
                }
            )
        if resolved_turn_number is not None:
            attributes["agentic.turn.number"] = resolved_turn_number
        return attributes

    @staticmethod
    def _nano(event: CanonicalEvent) -> int:
        return int(event.observed_at_unix_nano)

    @staticmethod
    def _duration_ms(
        event: CanonicalEvent, started_nano: int, provided: Any = None
    ) -> float:
        if isinstance(provided, (int, float)) and provided >= 0:
            # Hermes api_duration is seconds; tool/subagent duration_ms is ms.
            return float(provided)
        ended = SemanticEngine._nano(event)
        return (
            max(0.0, (ended - started_nano) / 1_000_000)
            if ended and started_nano
            else 0.0
        )

    def _session(self, event: CanonicalEvent) -> SessionState:
        return self.state.get_or_create_session(
            event.session_id,
            model=str(event.payload.get("model") or ""),
            platform=str(event.payload.get("platform") or ""),
        )

    def _turn(self, event: CanonicalEvent, *, create: bool) -> TurnState | None:
        session = self._session(event)
        source_id = str(
            event.correlation.get("turn_id")
            or event.correlation.get("parent_turn_id")
            or event.correlation.get("task_id")
            or ""
        )
        active = session.active_turn
        if active and (not source_id or active.source_id == source_id):
            return active
        if active and source_id and active.source_id != source_id:
            self._close_turn(session, event.timestamp, "superseded", event)
        if not create:
            return None
        next_number = session.turn_counter + 1
        if not source_id:
            material = f"{event.session_id}\0{event.event_id}\0{next_number}"
            digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
            source_id = f"canonical-turn-{digest}"
        number = (
            self._turn_number_allocator(event.session_id, source_id)
            if self._turn_number_allocator else next_number
        )
        session.turn_counter = number
        key = f"turn:{event.session_id}:{source_id}"
        turn = TurnState(
            key=key,
            source_id=source_id,
            span_key=key,
            number=session.turn_counter,
            started_at=event.timestamp,
            start_unix_nano=self._nano(event),
        )
        session.active_turn = turn
        self._auxiliary_turns[(event.session_id, source_id)] = TurnState(
            key=key, source_id=source_id, span_key=key, number=turn.number,
            started_at=turn.started_at, start_unix_nano=turn.start_unix_nano,
        )
        while len(self._auxiliary_turns) > self._auxiliary_turn_limit:
            self._auxiliary_turns.popitem(last=False)
        if session.first_turn_id is None:
            session.first_turn_id = turn.source_id
            session.first_turn_key = turn.span_key
        session.last_turn_id = turn.source_id
        session.last_turn_key = turn.span_key
        attrs = {
            **self._span_common(event.session_id, "CHAT", "invoke_agent", turn),
            "agentic.turn.number": turn.number,
        }
        self._span_start(
            event,
            key=key,
            parent=None,
            name=f"Turn {turn.number}",
            kind="CHAT",
            attributes=attrs,
        )
        if session.start_event is not None and not session.session_start_emitted:
            self._event(
                session.start_event,
                name="session_start",
                owner=turn.span_key,
                operation="chat",
                turn=turn,
                attributes={"gen_ai.session.id": session.session_id},
            )
            session.session_start_emitted = True
        return turn

    def _on_on_session_start(self, event: CanonicalEvent) -> None:
        if event.session_id in self._terminal_sessions:
            self._diagnostic(event, "session_start_after_terminal", event.session_id)
            return
        session = self._session(event)
        if session.start_event is not None:
            self._diagnostic(event, "duplicate_session_start", event.session_id)
            return
        session.start_event = event
        self._metric(
            event,
            name="agent.session.count",
            value=1,
            metric_kind="counter",
            unit="{count}",
            attributes={
                "ant.agent.type": self.config.agent_type,
                "ant.username": self.config.username,
            },
        )

    def _on_pre_llm_call(self, event: CanonicalEvent) -> None:
        turn = self._turn(event, create=True)
        assert turn is not None
        turn.user_message = event.payload.get("user_message")
        attrs: dict[str, Any] = {}
        if "user_message" in event.payload:
            attrs["gen_ai.input.messages"] = canonical_json(
                [{"role": "user", "content": event.payload["user_message"]}]
            )
        self._event(
            event,
            name="user_prompt",
            owner=turn.span_key,
            operation="chat",
            turn=turn,
            attributes=attrs,
        )

    def _on_post_llm_call(self, event: CanonicalEvent) -> None:
        turn = self._turn(event, create=False)
        if turn is None:
            self._diagnostic(event, "post_llm_without_turn")
            return
        turn.assistant_response = event.payload.get("assistant_response")

    def _on_pre_api_request(self, event: CanonicalEvent) -> None:
        self._start_api(event)

    def _on_pre_auxiliary_call(self, event: CanonicalEvent) -> None:
        if not self._has_auxiliary_parent(event):
            return
        self._start_api(event, auxiliary=True)

    def _has_auxiliary_parent(self, event: CanonicalEvent) -> bool:
        source_id = event.correlation.get("turn_id")
        if not source_id:
            self._diagnostic(event, "auxiliary_missing_turn_identity")
            return False
        if (event.session_id, str(source_id)) not in self._auxiliary_turns:
            self._diagnostic(event, "auxiliary_missing_active_turn")
            return False
        return True

    @staticmethod
    def _api_state_id(event: CanonicalEvent, auxiliary: bool) -> str:
        request_id = str(event.correlation.get("api_request_id") or "")
        if not auxiliary or not request_id:
            return request_id
        retry = event.payload.get("retry_count")
        if not isinstance(retry, int) or isinstance(retry, bool) or retry < 0:
            return ""
        return f"aux:{request_id}:{event.payload.get('aux_task') or 'unknown'}:{retry}"

    def _start_api(self, event: CanonicalEvent, *, auxiliary: bool = False) -> None:
        turn = (self._auxiliary_turns.get((event.session_id, str(event.correlation.get("turn_id") or "")))
                if auxiliary else self._turn(event, create=True))
        assert turn is not None
        request_id = self._api_state_id(event, auxiliary)
        if not request_id:
            self._diagnostic(event, "api_start_missing_request_id")
            return
        if request_id in turn.api_requests:
            self._diagnostic(event, "duplicate_api_start", request_id)
            return
        session = self.state.sessions.get(event.session_id)
        model = str(event.payload.get("model") or (session.model if session else "") or "unknown")
        provider = str(event.payload.get("provider") or "unknown")
        request_messages = event.payload.get("request_messages")
        if not isinstance(request_messages, list):
            request_messages = (
                [{"role": "user", "content": turn.user_message}]
                if turn.user_message is not None
                else []
            )
        system_instructions = "\n".join(
            str(message.get("content") or "")
            for message in request_messages
            if isinstance(message, dict)
            and message.get("role") in {"system", "developer"}
        )
        request = event.payload.get("request")
        if isinstance(request, dict):
            candidate_body = request.get("body")
            body = candidate_body if isinstance(candidate_body, dict) else request
            source_system = body.get("system") or body.get("instructions")
            if isinstance(source_system, str) and source_system:
                system_instructions = source_system
        source_system = event.payload.get("system_prompt")
        if isinstance(source_system, str) and source_system:
            # Auxiliary Responses hooks expose instructions separately from
            # messages; the explicit field also survives privacy as a marker.
            system_instructions = source_system
        key = f"api:{event.session_id}:{request_id}"
        state = ApiRequestState(
            key=key,
            request_id=request_id,
            span_key=key,
            turn_key=turn.span_key,
            model=model,
            provider=provider,
            input_messages=request_messages,
            system_instructions=system_instructions,
            started_at=event.timestamp,
            start_unix_nano=self._nano(event),
        )
        turn.api_requests[request_id] = state
        attrs = {
            **self._span_common(event.session_id, "LLM", "chat", turn),
            "gen_ai.request.model": model,
            "gen_ai.provider.name": provider,
            "agentic.model_call.id": str(event.correlation.get("api_request_id") or ""),
        }
        if auxiliary:
            attrs["agentic.llm.retry_count"] = event.payload["retry_count"]
            if event.payload.get("aux_task"):
                attrs["gen_ai.agent.name"] = str(event.payload["aux_task"])
        attrs["gen_ai.input.messages"] = canonical_json(request_messages)
        if system_instructions:
            attrs["gen_ai.system_instructions"] = canonical_json(system_instructions)
        self._span_start(
            event,
            key=key,
            parent=turn.span_key,
            name=f"chat {model}",
            kind="LLM",
            attributes=attrs,
        )

    def _on_post_api_request(self, event: CanonicalEvent) -> None:
        self._finish_api(event, is_error=False)

    def _on_api_request_error(self, event: CanonicalEvent) -> None:
        self._finish_api(event, is_error=True)

    def _on_post_auxiliary_call(self, event: CanonicalEvent) -> None:
        if not self._has_auxiliary_parent(event):
            return
        self._finish_api(event, is_error=bool(event.payload.get("error")), auxiliary=True)

    def _finish_api(self, event: CanonicalEvent, *, is_error: bool, auxiliary: bool = False) -> None:
        turn = (self._auxiliary_turns.get((event.session_id, str(event.correlation.get("turn_id") or "")))
                if auxiliary else self._turn(event, create=False))
        request_id = self._api_state_id(event, auxiliary)
        state = turn.api_requests.get(request_id) if turn and request_id else None
        if state is None:
            self._diagnostic(event, "orphan_api_terminal", request_id)
            return
        if state.terminal:
            self._diagnostic(event, "duplicate_api_terminal", request_id)
            return
        duration_value = event.payload.get("api_duration")
        duration_ms = (
            float(duration_value) * 1000
            if isinstance(duration_value, (int, float)) and duration_value >= 0
            else self._duration_ms(event, state.start_unix_nano)
        )
        usage_value = event.payload.get("usage")
        usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}

        def token_value(name: str) -> int | None:
            value = usage.get(name)
            return (
                value
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                else None
            )

        fresh_input_tokens = token_value("input_tokens")
        cache_read_tokens = token_value("cache_read_input_tokens")
        if cache_read_tokens is None:
            cache_read_tokens = token_value("cache_read_tokens")
        cache_write_tokens = token_value("cache_creation_input_tokens")
        if cache_write_tokens is None:
            cache_write_tokens = token_value("cache_write_tokens")
        prompt_tokens = token_value("prompt_tokens")
        input_tokens = (
            fresh_input_tokens + (cache_read_tokens or 0) + (cache_write_tokens or 0)
            if fresh_input_tokens is not None
            else prompt_tokens
        )
        # OpenAI prompt_tokens includes cache subsets; Anthropic input_tokens
        # reports fresh input separately. Preserve the total before deriving
        # fresh input, and do not emit a negative metric for inconsistent data.
        if fresh_input_tokens is None and prompt_tokens is not None:
            reported_cache = (cache_read_tokens or 0) + (cache_write_tokens or 0)
            if reported_cache <= prompt_tokens:
                fresh_input_tokens = prompt_tokens - reported_cache
        output_tokens = token_value("output_tokens")
        if output_tokens is None:
            output_tokens = token_value("completion_tokens")
        reasoning_tokens = token_value("reasoning_tokens")
        error = event.payload.get("error")
        error_type, error_message = self._error_fields(
            error, event.payload.get("reason")
        )
        category = str(event.payload.get("error_category") or classify_error(error))
        if category not in ERROR_CATEGORIES:
            category = "other"
        finish_reason = str(
            event.payload.get("finish_reason") or ("error" if is_error else "stop")
        )
        end_attrs: dict[str, Any] = {
            "agentic.model_call.id": str(event.correlation.get("api_request_id") or ""),
            "agentic.llm.duration_ms": duration_ms,
            "gen_ai.response.model": str(
                event.payload.get("response_model") or state.model
            ),
            "gen_ai.response.finish_reasons": canonical_json([finish_reason]),
        }
        if input_tokens is not None:
            end_attrs["gen_ai.usage.input_tokens"] = input_tokens
        else:
            end_attrs["agentic.usage.input_tokens.missing"] = "true"
        if output_tokens is not None:
            end_attrs["gen_ai.usage.output_tokens"] = output_tokens
        else:
            end_attrs["agentic.usage.output_tokens.missing"] = "true"
        if "assistant_message" in event.payload:
            end_attrs["gen_ai.output.messages"] = canonical_json(
                [event.payload["assistant_message"]]
            )
        if cache_read_tokens is not None:
            end_attrs["gen_ai.usage.cache_read.input_tokens"] = cache_read_tokens
            end_attrs["gen_ai.usage.details.cache_read_input_tokens"] = (
                cache_read_tokens
            )
        if cache_write_tokens is not None:
            end_attrs["gen_ai.usage.cache_creation.input_tokens"] = cache_write_tokens
            end_attrs["gen_ai.usage.details.cache_creation_input_tokens"] = (
                cache_write_tokens
            )
        if reasoning_tokens is not None:
            end_attrs["gen_ai.usage.reasoning.output_tokens"] = reasoning_tokens
        if is_error:
            end_attrs["error.type"] = error_type
            if error_message:
                end_attrs["error.message"] = error_message
        self._span_end(
            event.timestamp,
            key=state.span_key,
            name=f"chat {state.model}",
            attributes=end_attrs,
            status="ERROR" if is_error else "OK",
        )
        api_attrs: dict[str, Any] = {
            "agentic.model_call.id": str(event.correlation.get("api_request_id") or ""),
            "gen_ai.request.model": state.model,
            "gen_ai.provider.name": state.provider,
            "duration_ms": duration_ms,
            **(
                {"gen_ai.usage.input_tokens": input_tokens}
                if input_tokens is not None
                else {"agentic.usage.input_tokens.missing": "true"}
            ),
            **(
                {"gen_ai.usage.output_tokens": output_tokens}
                if output_tokens is not None
                else {"agentic.usage.output_tokens.missing": "true"}
            ),
        }
        if not is_error:
            assistant_message = event.payload.get("assistant_message")
            api_attrs.update(
                {
                    "gen_ai.response.model": str(
                        event.payload.get("response_model") or state.model
                    ),
                    "gen_ai.input.messages": canonical_json(state.input_messages),
                    "gen_ai.output.messages": canonical_json(
                        [assistant_message] if assistant_message is not None else []
                    ),
                    "gen_ai.system_instructions": canonical_json(
                        state.system_instructions
                    ),
                }
            )
            if input_tokens is not None and output_tokens is not None:
                api_attrs["gen_ai.usage.total_tokens"] = input_tokens + output_tokens
            if fresh_input_tokens is not None:
                api_attrs["agentic.usage.fresh_input_tokens"] = fresh_input_tokens
            if cache_read_tokens is not None:
                api_attrs["gen_ai.usage.cache_read.input_tokens"] = cache_read_tokens
                api_attrs["agentic.usage.cache_read_tokens"] = cache_read_tokens
            if cache_write_tokens is not None:
                api_attrs["gen_ai.usage.cache_creation.input_tokens"] = (
                    cache_write_tokens
                )
                api_attrs["agentic.usage.cache_write_tokens"] = cache_write_tokens
            if reasoning_tokens is not None:
                api_attrs["gen_ai.usage.reasoning.output_tokens"] = reasoning_tokens
        if is_error:
            api_attrs["error.type"] = error_type
            if error_message:
                api_attrs["error.message"] = error_message
            if isinstance(event.payload.get("status_code"), int):
                api_attrs["http.response.status_code"] = event.payload["status_code"]
        self._event(
            event,
            name="api_request",
            owner=state.span_key,
            operation="chat",
            turn=turn,
            attributes=api_attrs,
        )
        metric_attrs = {
            "gen_ai.request.model": state.model,
            "gen_ai.provider.name": state.provider,
            "ant.username": self.config.username,
        }
        self._metric(
            event,
            name="agent.api_request.count",
            value=1,
            metric_kind="counter",
            unit="{count}",
            attributes=metric_attrs,
        )
        self._metric(
            event,
            name="agent.api_request.duration",
            value=duration_ms,
            metric_kind="histogram",
            unit="ms",
            attributes=metric_attrs,
        )
        if is_error:
            self._metric(
                event,
                name="agent.api_request.error.count",
                value=1,
                metric_kind="counter",
                unit="{count}",
                attributes={
                    "gen_ai.request.model": state.model,
                    "ant.username": self.config.username,
                    "agentic.error.category": category,
                },
            )
        else:
            token_input = fresh_input_tokens
            token_attributes = {"ant.agent.type": self.config.agent_type}
            if token_input is not None and token_input > 0:
                self._metric(
                    event,
                    name="agent.token.usage",
                    value=token_input,
                    metric_kind="counter",
                    unit="{token}",
                    attributes={**token_attributes, "type": "input"},
                )
            if output_tokens is not None and output_tokens > 0:
                self._metric(
                    event,
                    name="agent.token.usage",
                    value=output_tokens,
                    metric_kind="counter",
                    unit="{token}",
                    attributes={**token_attributes, "type": "output"},
                )
            cache_attributes = {
                "gen_ai.request.model": state.model,
                "ant.agent.type": self.config.agent_type,
            }
            if cache_read_tokens is not None and cache_read_tokens > 0:
                self._metric(
                    event,
                    name="agent.cache.read_tokens",
                    value=cache_read_tokens,
                    metric_kind="counter",
                    unit="{token}",
                    attributes=cache_attributes,
                )
            if cache_write_tokens is not None and cache_write_tokens > 0:
                self._metric(
                    event,
                    name="agent.cache.write_tokens",
                    value=cache_write_tokens,
                    metric_kind="counter",
                    unit="{token}",
                    attributes=cache_attributes,
                )
        state.terminal = True
        assert turn is not None
        turn.api_requests.pop(request_id, None)

    def _on_pre_tool_call(self, event: CanonicalEvent) -> None:
        turn = self._turn(event, create=True)
        assert turn is not None
        call_id = str(event.correlation.get("tool_call_id") or "")
        if not call_id:
            self._diagnostic(event, "tool_start_missing_call_id")
            return
        if call_id in turn.tools:
            self._diagnostic(event, "duplicate_tool_start", call_id)
            return
        name = str(event.payload.get("tool_name") or "unknown")
        tool_type = str(event.correlation.get("tool_type") or "builtin")
        if tool_type not in {"builtin", "mcp", "skill"}:
            tool_type = "builtin"
        synthetic = bool(event.correlation.get("tool_call_id_synthetic"))
        key = f"tool:{event.session_id}:{call_id}"
        arguments_present = "args" in event.payload
        arguments = event.payload.get("args")
        state = ToolCallState(
            key=key,
            call_id=call_id,
            span_key=key,
            turn_key=turn.span_key,
            name=name,
            tool_type=tool_type,
            synthetic=synthetic,
            arguments=arguments,
            arguments_present=arguments_present,
            started_at=event.timestamp,
            start_unix_nano=self._nano(event),
        )
        turn.tools[call_id] = state
        attrs = {
            **self._span_common(event.session_id, "TOOL", "execute_tool", turn),
            **self._tool_identity(state),
            **(
                {"gen_ai.tool.call.arguments": canonical_json(arguments)}
                if arguments_present
                else {"agentic.tool.input.missing": "true"}
            ),
        }
        if event.projection.get("field_status", {}).get("args", {}).get("truncated"):
            attrs["agentic.tool.input.truncated"] = "true"
        self._span_start(
            event,
            key=key,
            parent=turn.span_key,
            name=f"{tool_type} {name}",
            kind="TOOL",
            attributes=attrs,
        )
        self._event(
            event,
            name="tool_call",
            owner=key,
            operation="execute_tool",
            turn=turn,
            attributes={
                **self._tool_identity(state),
                **(
                    {"gen_ai.tool.call.arguments": canonical_json(arguments)}
                    if arguments_present
                    else {"agentic.tool.input.missing": "true"}
                ),
                **(
                    {"agentic.tool.input.truncated": "true"}
                    if event.projection.get("field_status", {})
                    .get("args", {})
                    .get("truncated")
                    else {}
                ),
            },
        )

    def _on_post_tool_call(self, event: CanonicalEvent) -> None:
        turn = self._turn(event, create=False)
        call_id = str(event.correlation.get("tool_call_id") or "")
        if event.correlation.get("tool_match_ambiguous"):
            self._diagnostic(
                event,
                "ambiguous_tool_terminal",
                canonical_json(event.correlation["tool_match_ambiguous"]),
            )
            return
        state = turn.tools.get(call_id) if turn and call_id else None
        if state is None:
            self._diagnostic(event, "orphan_tool_terminal", call_id)
            return
        if state.terminal:
            self._diagnostic(event, "duplicate_tool_terminal", call_id)
            return
        duration_ms = self._duration_ms(
            event, state.start_unix_nano, event.payload.get("duration_ms")
        )
        status_text = str(event.payload.get("status") or "").casefold()
        error_type = str(event.payload.get("error_type") or "")
        error_message = str(event.payload.get("error_message") or "")
        is_error = bool(
            error_type or error_message or status_text in {"error", "failed", "failure"}
        )
        if is_error and not error_type:
            error_type = "tool_error"
        result_present = "result" in event.payload
        result = event.payload.get("result")
        end_attrs: dict[str, Any] = {
            "agentic.tool.duration_ms": duration_ms,
            "gen_ai.tool.call.result": canonical_json(result)
            if result_present
            else canonical_json(None),
        }
        if event.projection.get("field_status", {}).get("result", {}).get("truncated"):
            end_attrs["agentic.tool.output.truncated"] = "true"
        if is_error:
            end_attrs["error.type"] = error_type
            if error_message:
                end_attrs["error.message"] = error_message
        self._span_end(
            event.timestamp,
            key=state.span_key,
            name=f"{state.tool_type} {state.name}",
            attributes=end_attrs,
            status="ERROR" if is_error else "OK",
        )
        event_attrs = {
            **self._tool_identity(state),
            "duration_ms": duration_ms,
            "gen_ai.tool.call.arguments": (
                canonical_json(state.arguments)
                if state.arguments_present
                else canonical_json(None)
            ),
            "gen_ai.tool.call.result": canonical_json(result)
            if result_present
            else canonical_json(None),
        }
        if is_error:
            event_attrs["error.type"] = error_type
            if error_message:
                event_attrs["error.message"] = error_message
        self._event(
            event,
            name="tool_result",
            owner=state.span_key,
            operation="execute_tool",
            turn=turn,
            attributes=event_attrs,
        )
        metric_attrs = {
            "agentic.tool.type": state.tool_type,
            "ant.username": self.config.username,
            "gen_ai.conversation.id": event.session_id,
        }
        self._metric(
            event,
            name="agent.tool_call.count",
            value=1,
            metric_kind="counter",
            unit="{count}",
            attributes=metric_attrs,
        )
        self._metric(
            event,
            name="agent.tool_call.duration",
            value=duration_ms,
            metric_kind="histogram",
            unit="ms",
            attributes=metric_attrs,
        )
        if is_error:
            category = classify_error(error_type or error_message)
            if category in {"api_error", "other"}:
                category = "tool_error"
            self._metric(
                event,
                name="agent.tool_call.error.count",
                value=1,
                metric_kind="counter",
                unit="{count}",
                attributes={
                    "agentic.tool.type": state.tool_type,
                    "ant.username": self.config.username,
                    "agentic.error.category": category,
                },
            )
        state.terminal = True
        assert turn is not None
        turn.tools.pop(call_id, None)

    def _on_subagent_start(self, event: CanonicalEvent) -> None:
        turn = self._turn(event, create=True)
        assert turn is not None
        child_session = str(event.correlation.get("child_session_id") or "")
        child_id = str(event.correlation.get("child_subagent_id") or "")
        role = str(event.payload.get("child_role") or "unknown")
        if not child_session or not child_id:
            self._diagnostic(event, "subagent_start_missing_identity")
            return
        if child_session in turn.subagents:
            self._diagnostic(event, "duplicate_subagent_start", child_session)
            return
        key = f"agent:{event.session_id}:{child_session}"
        state = SubagentState(
            key=key,
            span_key=key,
            turn_key=turn.span_key,
            child_session_id=child_session,
            child_agent_id=child_id,
            child_role=role,
            started_at=event.timestamp,
            start_unix_nano=self._nano(event),
        )
        turn.subagents[child_session] = state
        identity = self._subagent_identity(state)
        attrs = {
            **self._span_common(event.session_id, "AGENT", "invoke_agent", turn),
            **identity,
        }
        self._span_start(
            event,
            key=key,
            parent=turn.span_key,
            name=f"agent {role}",
            kind="AGENT",
            attributes=attrs,
        )
        self._event(
            event,
            name="subagent_spawned",
            owner=key,
            operation="invoke_agent",
            turn=turn,
            attributes=identity,
        )

    def _on_subagent_stop(self, event: CanonicalEvent) -> None:
        turn = self._turn(event, create=False)
        child_session = str(event.correlation.get("child_session_id") or "")
        state = turn.subagents.get(child_session) if turn and child_session else None
        if state is None:
            self._diagnostic(event, "orphan_or_ambiguous_subagent_stop", child_session)
            return
        duration_ms = self._duration_ms(
            event, state.start_unix_nano, event.payload.get("duration_ms")
        )
        child_status = str(event.payload.get("child_status") or "completed")
        is_error = child_status.casefold() in {
            "failed",
            "error",
            "cancelled",
            "interrupted",
        }
        end_attrs: dict[str, Any] = {
            "agentic.subagent.duration_ms": duration_ms,
            "agentic.subagent.outcome": child_status,
        }
        if "child_summary" in event.payload:
            end_attrs["gen_ai.output.messages"] = canonical_json(
                [{"role": "assistant", "content": event.payload["child_summary"]}]
            )
        if is_error:
            end_attrs["error.type"] = f"subagent_{child_status.casefold()}"
        self._span_end(
            event.timestamp,
            key=state.span_key,
            name=f"agent {state.child_role}",
            attributes=end_attrs,
            status="ERROR" if is_error else "OK",
        )
        self._event(
            event,
            name="subagent_ended",
            owner=state.span_key,
            operation="invoke_agent",
            turn=turn,
            attributes={
                **self._subagent_identity(state),
                "duration_ms": duration_ms,
                "agentic.subagent.outcome": child_status,
            },
        )
        state.terminal = True
        assert turn is not None
        turn.subagents.pop(child_session, None)

    def _on_on_session_end(self, event: CanonicalEvent) -> None:
        session = self._session(event)
        if session.active_turn is None:
            self._diagnostic(event, "turn_end_without_active_turn")
            return
        reason = "interrupted" if event.payload.get("interrupted") else "completed"
        self._close_turn(session, event.timestamp, reason, event)

    def _on_on_session_finalize(self, event: CanonicalEvent) -> None:
        session = self.state.sessions.get(event.session_id)
        if session is None:
            self._diagnostic(event, "finalize_without_active_session", event.session_id)
            return
        self._finalize_session(
            session,
            event.timestamp,
            str(event.payload.get("reason") or "finalize"),
            event,
        )

    def _on_on_session_reset(self, event: CanonicalEvent) -> None:
        session = self.state.sessions.get(event.session_id)
        if session is None:
            # Hermes v0.18.2 rotates to the new session id before firing reset.
            # The preceding id has already received on_session_finalize, so do
            # not invent a session_end for a session that has not started.
            self._diagnostic(event, "reset_without_active_session", event.session_id)
            return
        self._finalize_session(
            session, event.timestamp, str(event.payload.get("reason") or "reset"), event
        )

    def _close_turn(
        self,
        session: SessionState,
        timestamp: str,
        reason: str,
        event: CanonicalEvent | None,
    ) -> None:
        turn = session.active_turn
        if turn is None or turn.terminal:
            return
        for state in list(turn.api_requests.values()):
            self._cancel_api(session, turn, state, timestamp, reason, event)
        for state in list(turn.tools.values()):
            self._cancel_tool(session, turn, state, timestamp, reason, event)
        for state in list(turn.subagents.values()):
            self._cancel_subagent(session, turn, state, timestamp, reason, event)
        attrs: dict[str, Any] = {"agentic.turn.number": turn.number}
        if turn.user_message is not None:
            attrs["gen_ai.input.messages"] = canonical_json(
                [{"role": "user", "content": turn.user_message}]
            )
        if turn.assistant_response is not None:
            attrs["gen_ai.output.messages"] = canonical_json(
                [{"role": "assistant", "content": turn.assistant_response}]
            )
        self._span_end(
            timestamp,
            key=turn.span_key,
            name=f"Turn {turn.number}",
            attributes=attrs,
            status="OK" if reason == "completed" else "ERROR",
        )
        turn.terminal = True
        session.last_turn_id = turn.source_id
        session.last_turn_key = turn.span_key
        session.active_turn = None

    def _finalize_session(
        self,
        session: SessionState,
        timestamp: str,
        reason: str,
        event: CanonicalEvent | None,
    ) -> None:
        if session.session_id in self._terminal_sessions or session.terminal:
            if event:
                self._diagnostic(
                    event, "duplicate_session_terminal", session.session_id
                )
            self.state.sessions.pop(session.session_id, None)
            return
        self._close_turn(session, timestamp, reason, event)
        # A failed turn allocation (or a session without any turn) has no
        # valid owner/ordinal for a session_end event. Preserve diagnostics
        # without inventing turn zero or a detached lifecycle event.
        if event and session.last_turn_id and not session.session_end_emitted:
            self._event(
                event,
                name="session_end",
                owner=session.last_turn_key,
                operation="chat",
                turn=None,
                attributes={
                    "gen_ai.session.id": session.session_id,
                    "agentic.turn.number": session.turn_counter,
                },
                turn_id=session.last_turn_id,
                turn_number=session.turn_counter,
            )
            session.session_end_emitted = True
        session.terminal = True
        self._terminal_sessions.add(session.session_id)
        self.state.sessions.pop(session.session_id, None)

    def _cancel_api(
        self,
        session: SessionState,
        turn: TurnState,
        state: ApiRequestState,
        timestamp: str,
        reason: str,
        event: CanonicalEvent | None,
    ) -> None:
        duration_ms = (
            max(
                0.0,
                (int(event.observed_at_unix_nano) - state.start_unix_nano) / 1_000_000,
            )
            if event is not None and state.start_unix_nano
            else 0.0
        )
        self._span_end(
            timestamp,
            key=state.span_key,
            name=f"chat {state.model}",
            status="ERROR",
            attributes={
                "agentic.llm.duration_ms": duration_ms,
                "agentic.usage.input_tokens.missing": "true",
                "agentic.usage.output_tokens.missing": "true",
                "gen_ai.response.model": state.model,
                "gen_ai.response.finish_reasons": canonical_json(["cancelled"]),
                "error.type": "cancelled",
            },
        )
        if event is not None:
            self._event(
                event,
                name="api_request",
                owner=state.span_key,
                operation="chat",
                turn=turn,
                attributes={
                    "gen_ai.request.model": state.model,
                    "gen_ai.provider.name": state.provider,
                    "duration_ms": duration_ms,
                    "agentic.usage.input_tokens.missing": "true",
                    "agentic.usage.output_tokens.missing": "true",
                    "error.type": "cancelled",
                    "error.message": reason,
                },
            )
            metric_attrs = {
                "gen_ai.request.model": state.model,
                "gen_ai.provider.name": state.provider,
                "ant.username": self.config.username,
            }
            self._metric(
                event,
                name="agent.api_request.count",
                value=1,
                metric_kind="counter",
                unit="{count}",
                attributes=metric_attrs,
            )
            self._metric(
                event,
                name="agent.api_request.duration",
                value=duration_ms,
                metric_kind="histogram",
                unit="ms",
                attributes=metric_attrs,
            )
            self._metric(
                event,
                name="agent.api_request.error.count",
                value=1,
                metric_kind="counter",
                unit="{count}",
                attributes={
                    "gen_ai.request.model": state.model,
                    "ant.username": self.config.username,
                    "agentic.error.category": "cancelled",
                },
            )
        turn.api_requests.pop(state.request_id, None)

    def _cancel_tool(
        self,
        session: SessionState,
        turn: TurnState,
        state: ToolCallState,
        timestamp: str,
        reason: str,
        event: CanonicalEvent | None,
    ) -> None:
        duration_ms = (
            max(
                0.0,
                (int(event.observed_at_unix_nano) - state.start_unix_nano) / 1_000_000,
            )
            if event is not None and state.start_unix_nano
            else 0.0
        )
        self._span_end(
            timestamp,
            key=state.span_key,
            name=f"{state.tool_type} {state.name}",
            status="ERROR",
            attributes={
                "agentic.tool.duration_ms": duration_ms,
                "gen_ai.tool.call.result": canonical_json(None),
                "error.type": "cancelled",
            },
        )
        if event is not None:
            self._event(
                event,
                name="tool_result",
                owner=state.span_key,
                operation="execute_tool",
                turn=turn,
                attributes={
                    **self._tool_identity(state),
                    "duration_ms": duration_ms,
                    "gen_ai.tool.call.result": canonical_json(None),
                    "error.type": "cancelled",
                    "error.message": reason,
                },
            )
            metric_attrs = {
                "agentic.tool.type": state.tool_type,
                "ant.username": self.config.username,
                "gen_ai.conversation.id": session.session_id,
            }
            self._metric(
                event,
                name="agent.tool_call.count",
                value=1,
                metric_kind="counter",
                unit="{count}",
                attributes=metric_attrs,
            )
            self._metric(
                event,
                name="agent.tool_call.duration",
                value=duration_ms,
                metric_kind="histogram",
                unit="ms",
                attributes=metric_attrs,
            )
            self._metric(
                event,
                name="agent.tool_call.error.count",
                value=1,
                metric_kind="counter",
                unit="{count}",
                attributes={
                    "agentic.tool.type": state.tool_type,
                    "ant.username": self.config.username,
                    "agentic.error.category": "cancelled",
                },
            )
        turn.tools.pop(state.call_id, None)

    def _cancel_subagent(
        self,
        session: SessionState,
        turn: TurnState,
        state: SubagentState,
        timestamp: str,
        reason: str,
        event: CanonicalEvent | None,
    ) -> None:
        duration_ms = (
            max(
                0.0,
                (int(event.observed_at_unix_nano) - state.start_unix_nano) / 1_000_000,
            )
            if event is not None and state.start_unix_nano
            else 0.0
        )
        self._span_end(
            timestamp,
            key=state.span_key,
            name=f"agent {state.child_role}",
            status="ERROR",
            attributes={
                "agentic.subagent.duration_ms": duration_ms,
                "agentic.subagent.outcome": "cancelled",
                "error.type": "cancelled",
            },
        )
        if event is not None:
            self._event(
                event,
                name="subagent_ended",
                owner=state.span_key,
                operation="invoke_agent",
                turn=turn,
                attributes={
                    **self._subagent_identity(state),
                    "duration_ms": duration_ms,
                    "agentic.subagent.outcome": "cancelled",
                },
            )
        turn.subagents.pop(state.child_session_id, None)

    @staticmethod
    def _tool_identity(state: ToolCallState) -> dict[str, Any]:
        return {
            "gen_ai.tool.name": state.name,
            "gen_ai.tool.call.id": state.call_id,
            "agentic.tool.type": state.tool_type,
            "agentic.tool.name": state.name,
            "agentic.tool.raw_name": state.name,
            "agentic.tool.id": state.call_id,
            **({"agentic.tool.call_id.synthetic": "true"} if state.synthetic else {}),
        }

    @staticmethod
    def _subagent_identity(state: SubagentState) -> dict[str, Any]:
        return {
            "gen_ai.agent.id": state.child_agent_id,
            "gen_ai.agent.name": state.child_role,
            "agentic.subagent.session_key": state.child_session_id,
            "agentic.subagent.name": state.child_role,
        }

    @staticmethod
    def _error_fields(error: Any, fallback: Any = None) -> tuple[str, str]:
        if isinstance(error, dict):
            return str(error.get("type") or "api_error"), str(
                error.get("message") or fallback or ""
            )
        if error:
            return "api_error", str(error)
        return "api_error", str(fallback or "")

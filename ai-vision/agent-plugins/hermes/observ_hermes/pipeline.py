"""Canonical live-hook pipeline shared with offline replay semantics."""

from __future__ import annotations

import threading
from typing import Any, Callable, Mapping, cast

from aivision_hermes_core.normalize import CanonicalNormalizer
from aivision_hermes_core.semantic import (
    MemorySink,
    SemanticConfig,
    SemanticEngine,
    SemanticObservation,
)

from .config import ObservHermesConfig
from .turn_numbers import TurnNumberStore


def source_session_id(payload: Mapping[str, Any]) -> str:
    return str(
        payload.get("session_id")
        or payload.get("parent_session_id")
        or payload.get("child_session_id")
        or ""
    ).strip()


def semantic_config(config: ObservHermesConfig) -> SemanticConfig:
    """Build identity-neutral semantics shared by live and replay execution.

    Production identity is an emission concern.  It is projected by
    ``observ_hermes.telemetry`` so offline replay never needs production config.
    """
    return SemanticConfig(
        agent_type="hermes",
        agent_product="Hermes",
        runtime_name="hermes",
        collection_mode=config.collection_mode,
    )


class CanonicalPipeline:
    """Normalize live callbacks and apply their semantic consequences in memory."""

    def __init__(
        self,
        config: ObservHermesConfig,
        sink: Callable[[SemanticObservation], None] | None = None,
    ) -> None:
        self.config = config
        self.memory_sink = MemorySink() if sink is None else None
        self.sink = sink or self.memory_sink
        assert self.sink is not None
        # Loaded production config always has its Hermes-home path. Direct
        # in-memory consumers and replay do not implicitly acquire disk state.
        self._turn_numbers = (
            TurnNumberStore(config) if config.enabled and config.config_path else None
        )
        self.engine = SemanticEngine(
            semantic_config(config), self.sink,
            turn_number_allocator=self._turn_numbers.reserve if self._turn_numbers else None,
        )
        self._normalizers: dict[str, CanonicalNormalizer] = {}
        self._lock = threading.RLock()
        self._closed = False

    def process(self, hook: str, payload: Mapping[str, Any]) -> None:
        session_id = source_session_id(payload)
        if not session_id:
            raise ValueError(f"{hook} requires a documented Hermes session identifier")
        with self._lock:
            if self._closed:
                raise RuntimeError("canonical pipeline is closed")
            normalizer = self._normalizers.setdefault(
                session_id, CanonicalNormalizer(self.config.collection_mode)
            )
            event = normalizer.normalize(
                hook,
                payload,
                provenance={
                    "runtime": "hermes",
                    "observer_schema": payload.get(
                        "telemetry_schema_version", "hermes.observer.v1"
                    ),
                },
            )
            self.engine.apply(event)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._normalizers.clear()
            self.engine.shutdown()
            self._closed = True


def get_pipeline(runtime: Any) -> CanonicalPipeline:
    pipeline = getattr(runtime, "_observ_hermes_pipeline", None)
    if pipeline is None:
        candidate = getattr(runtime, "consume_observation", None)
        sink = (
            cast(Callable[[SemanticObservation], None], candidate)
            if callable(candidate)
            else None
        )
        pipeline = CanonicalPipeline(runtime.config, sink)
        setattr(runtime, "_observ_hermes_pipeline", pipeline)
    return pipeline


def submit_hook(runtime: Any, hook: str, payload: Mapping[str, Any]) -> None:
    get_pipeline(runtime).process(hook, payload)

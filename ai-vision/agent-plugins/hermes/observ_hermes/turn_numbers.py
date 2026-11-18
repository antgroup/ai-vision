"""Production-only durable numbering of observed native turns.

No transcript, host database, message history, or raw identifiers are stored.
The core and offline replay never open this collector-owned database.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from .config import ObservHermesConfig


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class TurnNumberStore:
    def __init__(self, config: ObservHermesConfig) -> None:
        self.path = Path(config.config_path).parent / "observ-hermes-state" / "turn-numbers.sqlite3"
        # Route/identity changes create a separate observation sequence. Only
        # the digest is persisted, including when headers contain credentials.
        self.scope = _digest({
            "identity": asdict(config.ant),
            "attributes": config.otel_exporter.attributes,
            "resource": config.otel_exporter.resource_attributes,
            "destinations": [
                {"endpoint": signal.endpoint or config.otel_exporter.endpoint,
                 "headers": {**config.otel_exporter.headers, **signal.headers}}
                for signal in (config.traces_config, config.metrics_config, config.events_config)
            ],
        })
        self._initialized = False

    def reserve(self, session_id: str, turn_id: str) -> int:
        """Commit an idempotent allocation before any telemetry is emitted.

        SQLite serializes allocation across CLI processes. Errors propagate to
        the plugin's existing fail-open hook boundary; never silently restart
        the numbering or overwrite a damaged database.
        """
        if not self._initialized:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
            self._initialized = True
        session = _digest([self.scope, session_id])
        turn = _digest(turn_id)
        with closing(sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True, timeout=1.0)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""CREATE TABLE IF NOT EXISTS turns (
                    session TEXT NOT NULL, turn TEXT NOT NULL, number INTEGER NOT NULL CHECK(number > 0),
                    PRIMARY KEY(session, turn), UNIQUE(session, number))""")
                existing = connection.execute(
                    "SELECT number FROM turns WHERE session = ? AND turn = ?", (session, turn)
                ).fetchone()
                if existing:
                    return int(existing[0])
                number = connection.execute(
                    "SELECT COALESCE(MAX(number), 0) + 1 FROM turns WHERE session = ?", (session,)
                ).fetchone()[0]
                connection.execute("INSERT INTO turns VALUES (?, ?, ?)", (session, turn, number))
                return int(number)

"""Independent, disabled-by-default configuration for hermes-replay."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ALLOWED_KEYS = {
    "captureEnabled",
    "collectionMode",
    "sessionDir",
    "sessionName",
    "debug",
}


class ReplayConfigError(ValueError):
    pass


def hermes_home(environ: Mapping[str, str], home: Path | None = None) -> Path:
    configured = environ.get("HERMES_HOME", "").strip()
    return (
        Path(configured).expanduser()
        if configured
        else (home or Path.home()) / ".hermes"
    )


def replay_config_path(environ: Mapping[str, str], home: Path | None = None) -> Path:
    return hermes_home(environ, home) / "hermes-replay.json"


def default_session_dir(environ: Mapping[str, str], home: Path | None = None) -> Path:
    configured = environ.get("AIVISION_HOME", "").strip()
    root = (
        Path(configured).expanduser()
        if configured
        else (home or Path.home()) / ".aivision"
    )
    return root / "hermes" / "transcripts"


def _bool(raw: str, name: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ReplayConfigError(f"{name} must be true or false")


def _json_bool(value: object, name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ReplayConfigError(f"{name} must be a boolean")
    return value


def _json_string(value: object, name: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ReplayConfigError(f"{name} must be a string")
    return value


def _explicit_non_empty_string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ReplayConfigError(f"{name} must be a string")
    if value == "":
        raise ReplayConfigError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class ReplayConfig:
    capture_enabled: bool = False
    collection_mode: str = "full"
    session_dir: Path | None = None
    session_name: str = ""
    debug: bool = False

    @classmethod
    def load(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        home: Path | None = None,
    ) -> "ReplayConfig":
        env = os.environ if environ is None else environ
        path = replay_config_path(env, home)
        value: dict[str, object] = {}
        if path.is_file():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ReplayConfigError(
                    f"cannot load {path}: {type(error).__name__}"
                ) from error
            if not isinstance(parsed, dict):
                raise ReplayConfigError(f"{path}: root must be an object")
            unknown = sorted(set(parsed) - ALLOWED_KEYS)
            if unknown:
                raise ReplayConfigError(f"{path}.{unknown[0]} is not supported")
            value = parsed

        capture = _json_bool(value.get("captureEnabled"), "captureEnabled", False)
        if "HERMES_REPLAY_CAPTURE_ENABLED" in env:
            capture = _bool(
                env["HERMES_REPLAY_CAPTURE_ENABLED"],
                "HERMES_REPLAY_CAPTURE_ENABLED",
            )
        configured_mode = _json_string(
            value.get("collectionMode"),
            "collectionMode",
            "full",
        )
        mode = env.get("HERMES_REPLAY_COLLECTION_MODE") or configured_mode
        if mode not in {"full", "privacy"}:
            raise ReplayConfigError("collectionMode must be full or privacy")
        debug = _json_bool(value.get("debug"), "debug", False)
        if "HERMES_REPLAY_DEBUG" in env:
            debug = _bool(env["HERMES_REPLAY_DEBUG"], "HERMES_REPLAY_DEBUG")
        configured_name = (
            _explicit_non_empty_string(value["sessionName"], "sessionName")
            if "sessionName" in value
            else ""
        )
        session_name = (
            _explicit_non_empty_string(
                env["HERMES_REPLAY_SESSION_NAME"],
                "HERMES_REPLAY_SESSION_NAME",
            )
            if "HERMES_REPLAY_SESSION_NAME" in env
            else configured_name
        )
        file_directory = (
            _explicit_non_empty_string(value["sessionDir"], "sessionDir")
            if "sessionDir" in value
            else ""
        )
        configured_dir = (
            _explicit_non_empty_string(
                env["HERMES_REPLAY_SESSION_DIR"],
                "HERMES_REPLAY_SESSION_DIR",
            )
            if "HERMES_REPLAY_SESSION_DIR" in env
            else file_directory
        )
        if not capture:
            return cls(False, mode, None, session_name, debug)
        session_dir = (
            Path(configured_dir).expanduser()
            if configured_dir
            else default_session_dir(env, home)
        )
        return cls(True, mode, session_dir, session_name, debug)

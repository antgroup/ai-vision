"""Strict common-schema configuration for the observ-hermes production plugin."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, MutableMapping

PUBLIC_KEYS = {
    "enableTelemetry",
    "serviceName",
    "collectionMode",
    "debug",
    "dynamicUsername",
    "dynamicUid",
    "otelExporter",
    "tracesConfig",
    "metricsConfig",
    "eventsConfig",
    "metricExportInterval",
    "logsExportInterval",
    "logDir",
}
EXPORTER_KEYS = {
    "endpoint",
    "protocol",
    "headers",
    "attributes",
    "resourceAttributes",
    "metricsTemporalityPreference",
}
SIGNAL_KEYS = {"endpoint", "headers", "enabled"}
COLLECTION_MODES = {"full", "privacy"}
PROTOCOLS = {"http/json", "http/protobuf"}
TEMPORALITIES = {"delta", "cumulative"}


DEFAULT_CONFIG: dict[str, Any] = {
    "enableTelemetry": False,
    "serviceName": "hermes",
    "collectionMode": "full",
    "debug": False,
    "dynamicUsername": False,
    "dynamicUid": False,
    "otelExporter": {
        "endpoint": "",
        "protocol": "http/json",
        "headers": {},
        "attributes": {},
        "resourceAttributes": {
            "ant.agent.type": "hermes",
            "ant.agent.product": "Hermes",
            "ant.agent.instance": "",
            "ant.agent.env": "Default",
            "ant.agent.scene": "default",
        },
        "metricsTemporalityPreference": "delta",
    },
    "tracesConfig": {"endpoint": "", "headers": {}, "enabled": True},
    "metricsConfig": {"endpoint": "", "headers": {}, "enabled": True},
    "eventsConfig": {"endpoint": "", "headers": {}, "enabled": True},
    "metricExportInterval": 60_000,
    "logsExportInterval": 5_000,
    "logDir": "",
}


class ConfigError(ValueError):
    """A production configuration source violates the common contract."""


def hermes_home(
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    configured = env.get("HERMES_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    return (Path.home() if home is None else home) / ".hermes"


def production_config_path(
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    return hermes_home(environ=environ, home=home) / "observ-hermes.json"


def default_log_dir(
    edition_name: str,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve the edition-owned production diagnostic directory."""
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    if edition_name == "aivision":
        configured = env.get("AIVISION_HOME", "").strip()
        root = Path(configured).expanduser() if configured else user_home / ".aivision"
        return root / "hermes" / "logs"
    return hermes_home(environ=env, home=user_home) / "logs"


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be an object")
    return value


def _string_map(value: Any, path: str) -> dict[str, str]:
    raw = _mapping(value, path)
    result: dict[str, str] = {}
    for key, child in raw.items():
        if not isinstance(key, str) or not isinstance(child, str):
            raise ConfigError(f"{path}.{key} must be a string")
        result[key] = child
    return result


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"{path}.{unknown[0]} is not a supported production field")


def validate_public_config(value: Any, source: str = "configuration") -> dict[str, Any]:
    config = _mapping(value, source)
    _reject_unknown(config, PUBLIC_KEYS, source)

    for key in ("enableTelemetry", "debug", "dynamicUsername", "dynamicUid"):
        if key in config and not isinstance(config[key], bool):
            raise ConfigError(f"{source}.{key} must be a boolean")
    if "serviceName" in config and not isinstance(config["serviceName"], str):
        raise ConfigError(f"{source}.serviceName must be a string")
    if "collectionMode" in config and config["collectionMode"] not in COLLECTION_MODES:
        raise ConfigError(f"{source}.collectionMode must be full or privacy")
    if "logDir" in config and not isinstance(config["logDir"], str):
        raise ConfigError(f"{source}.logDir must be a string")
    for key in ("metricExportInterval", "logsExportInterval"):
        if key in config:
            value = config[key]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ConfigError(f"{source}.{key} must be a positive integer")

    if "otelExporter" in config:
        exporter = _mapping(config["otelExporter"], f"{source}.otelExporter")
        _reject_unknown(exporter, EXPORTER_KEYS, f"{source}.otelExporter")
        for key in ("endpoint", "protocol", "metricsTemporalityPreference"):
            if key in exporter and not isinstance(exporter[key], str):
                raise ConfigError(f"{source}.otelExporter.{key} must be a string")
        if "protocol" in exporter and exporter["protocol"] not in PROTOCOLS:
            raise ConfigError(
                f"{source}.otelExporter.protocol must be http/json or http/protobuf"
            )
        if (
            "metricsTemporalityPreference" in exporter
            and exporter["metricsTemporalityPreference"] not in TEMPORALITIES
        ):
            raise ConfigError(
                f"{source}.otelExporter.metricsTemporalityPreference must be "
                "delta or cumulative"
            )
        for key in ("headers", "attributes", "resourceAttributes"):
            if key in exporter:
                _string_map(exporter[key], f"{source}.otelExporter.{key}")

    for signal_key in ("tracesConfig", "metricsConfig", "eventsConfig"):
        if signal_key not in config:
            continue
        signal = _mapping(config[signal_key], f"{source}.{signal_key}")
        _reject_unknown(signal, SIGNAL_KEYS, f"{source}.{signal_key}")
        if "endpoint" in signal and not isinstance(signal["endpoint"], str):
            raise ConfigError(f"{source}.{signal_key}.endpoint must be a string")
        if "headers" in signal:
            _string_map(signal["headers"], f"{source}.{signal_key}.headers")
        if "enabled" in signal and not isinstance(signal["enabled"], bool):
            raise ConfigError(f"{source}.{signal_key}.enabled must be a boolean")
    return dict(config)


def load_json_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot load {path}: {type(error).__name__}") from error
    return validate_public_config(parsed, str(path))


def _load_edition_config(
    edition_name: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    configs_dir: Path | None = None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    name = (
        edition_name
        or env.get("OBSERV_HERMES_EDITION", "").strip()
        or env.get("AIVISION_EDITION", "").strip()
        or "common"
    )
    if name not in {"common", "aivision"}:
        raise ConfigError(f"unsupported Hermes edition: {name}")
    root = Path(__file__).parent / "configs" if configs_dir is None else configs_dir
    path = root / f"{name}.json"
    if not path.is_file():
        raise ConfigError(f"Hermes edition config does not exist: {path}")
    return load_json_config(path)


def _resolve_edition_name(
    edition_name: str | None,
    environ: Mapping[str, str],
) -> str:
    return (
        edition_name
        or environ.get("OBSERV_HERMES_EDITION", "").strip()
        or environ.get("AIVISION_EDITION", "").strip()
        or "common"
    )


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        key: _deep_merge(value, {}) if isinstance(value, dict) else value
        for key, value in base.items()
    }
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = _deep_merge(value, {}) if isinstance(value, dict) else value
    return result


def _parse_pairs(raw: str, variable: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not raw.strip():
        return result
    for item in raw.split(","):
        if "=" not in item:
            raise ConfigError(f"{variable} must use key=value pairs")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ConfigError(f"{variable} contains an empty key")
        result[key] = value.strip()
    return result


def _first_env(
    env: Mapping[str, str], names: tuple[str, ...]
) -> tuple[str, str] | None:
    for name in names:
        value = env.get(name)
        if value is not None and value.strip() != "":
            return name, value.strip()
    return None


def _env_bool(env: Mapping[str, str], names: tuple[str, ...]) -> bool | None:
    found = _first_env(env, names)
    if found is None:
        return None
    name, raw = found
    normalized = raw.lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def _env_int(env: Mapping[str, str], names: tuple[str, ...]) -> int | None:
    found = _first_env(env, names)
    if found is None:
        return None
    name, raw = found
    try:
        parsed = int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a positive integer") from error
    if parsed <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return parsed


def _set_if_env(
    target: MutableMapping[str, Any],
    key: str,
    env: Mapping[str, str],
    names: tuple[str, ...],
) -> None:
    found = _first_env(env, names)
    if found is not None:
        target[key] = found[1]


def _merge_env_pairs(
    target: MutableMapping[str, str],
    env: Mapping[str, str],
    names_low_to_high: tuple[str, ...],
) -> None:
    for name in names_low_to_high:
        raw = env.get(name)
        if raw is not None and raw.strip() != "":
            target.update(_parse_pairs(raw, name))


def _apply_environment(config: dict[str, Any], env: Mapping[str, str]) -> None:
    boolean_sources = {
        "enableTelemetry": ("OBSERV_HERMES_ENABLE_TELEMETRY", "AGENT_ENABLE_TELEMETRY"),
        "debug": ("OBSERV_HERMES_DEBUG",),
        "dynamicUsername": (
            "OBSERV_HERMES_DYNAMIC_USERNAME",
            "AIVISION_DYNAMIC_USERNAME",
        ),
        "dynamicUid": ("OBSERV_HERMES_DYNAMIC_UID", "AIVISION_DYNAMIC_UID"),
    }
    for key, sources in boolean_sources.items():
        value = _env_bool(env, sources)
        if value is not None:
            config[key] = value

    _set_if_env(
        config, "serviceName", env, ("OBSERV_HERMES_SERVICE_NAME", "OTEL_SERVICE_NAME")
    )
    _set_if_env(
        config,
        "collectionMode",
        env,
        ("OBSERV_HERMES_COLLECTION_MODE", "AIVISION_COLLECTION_MODE"),
    )
    _set_if_env(config, "logDir", env, ("OBSERV_HERMES_LOG_DIR", "AIVISION_LOG_DIR"))

    for key, sources in {
        "metricExportInterval": (
            "OBSERV_HERMES_METRIC_EXPORT_INTERVAL",
            "OTEL_METRIC_EXPORT_INTERVAL",
        ),
        "logsExportInterval": (
            "OBSERV_HERMES_LOGS_EXPORT_INTERVAL",
            "OTEL_LOGS_EXPORT_INTERVAL",
        ),
    }.items():
        value = _env_int(env, sources)
        if value is not None:
            config[key] = value

    exporter = config["otelExporter"]
    _set_if_env(
        exporter,
        "endpoint",
        env,
        ("OBSERV_HERMES_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"),
    )
    _set_if_env(
        exporter,
        "protocol",
        env,
        ("OBSERV_HERMES_OTLP_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL"),
    )
    _set_if_env(
        exporter,
        "metricsTemporalityPreference",
        env,
        (
            "OBSERV_HERMES_METRICS_TEMPORALITY_PREFERENCE",
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
        ),
    )
    _merge_env_pairs(
        exporter["headers"],
        env,
        ("OTEL_EXPORTER_OTLP_HEADERS", "OBSERV_HERMES_OTLP_HEADERS"),
    )
    _merge_env_pairs(
        exporter["attributes"],
        env,
        ("AIVISION_OTEL_ATTRIBUTES", "OBSERV_HERMES_OTEL_ATTRIBUTES"),
    )
    _merge_env_pairs(
        exporter["resourceAttributes"],
        env,
        (
            "OTEL_RESOURCE_ATTRIBUTES",
            "AIVISION_OTEL_RESOURCE_ATTRIBUTES",
            "OBSERV_HERMES_OTEL_RESOURCE_ATTRIBUTES",
        ),
    )

    identity_attributes = exporter["attributes"]
    _set_if_env(
        identity_attributes,
        "ant.username",
        env,
        ("OBSERV_HERMES_USERNAME", "AIVISION_ANT_USERNAME", "ANT_USERNAME"),
    )
    _set_if_env(
        identity_attributes,
        "ant.uid",
        env,
        ("OBSERV_HERMES_USER_ID", "AIVISION_ANT_UID", "AIVISION_USER_ID"),
    )

    resource_env = {
        "ant.agent.type": ("OBSERV_HERMES_AGENT_TYPE", "AIVISION_AGENT_TYPE"),
        "ant.agent.product": ("OBSERV_HERMES_AGENT_PRODUCT", "AIVISION_AGENT_PRODUCT"),
        "ant.agent.instance": (
            "OBSERV_HERMES_AGENT_INSTANCE",
            "AIVISION_AGENT_INSTANCE",
        ),
        "ant.agent.env": ("OBSERV_HERMES_AGENT_ENV", "AIVISION_AGENT_ENV"),
        "ant.agent.scene": ("OBSERV_HERMES_AGENT_SCENE", "AIVISION_AGENT_SCENE"),
    }
    for key, sources in resource_env.items():
        _set_if_env(exporter["resourceAttributes"], key, env, sources)

    signal_specs = {
        "tracesConfig": "TRACES",
        "metricsConfig": "METRICS",
        "eventsConfig": "LOGS",
    }
    for config_key, signal_name in signal_specs.items():
        signal = config[config_key]
        _set_if_env(
            signal,
            "endpoint",
            env,
            (
                f"OBSERV_HERMES_OTLP_{signal_name}_ENDPOINT",
                f"OTEL_EXPORTER_OTLP_{signal_name}_ENDPOINT",
            ),
        )
        _merge_env_pairs(
            signal["headers"],
            env,
            (
                f"OTEL_EXPORTER_OTLP_{signal_name}_HEADERS",
                f"OBSERV_HERMES_OTLP_{signal_name}_HEADERS",
            ),
        )
        enabled = _env_bool(env, (f"OBSERV_HERMES_{signal_name}_ENABLED",))
        if enabled is not None:
            signal["enabled"] = enabled


def _hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown"


@dataclass
class SignalEndpointConfig:
    endpoint: str = ""
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    enabled: bool = True


@dataclass
class AntIdentity:
    """Internal compatibility projection; it is not a public config object."""

    username: str = ""
    uid: str = ""
    agent_type: str = "hermes"
    agent_product: str = "Hermes"
    agent_instance: str = ""
    public_key: str = ""
    dynamic_username: bool = False
    dynamic_uid: bool = False
    agent_env: str = "Default"
    agent_scene: str = "default"


@dataclass
class OtelExporterConfig:
    endpoint: str = ""
    protocol: str = "http/json"
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    attributes: dict[str, str] = field(default_factory=dict, repr=False)
    resource_attributes: dict[str, str] = field(default_factory=dict, repr=False)
    metrics_temporality_preference: str = "delta"


@dataclass
class ObservHermesConfig:
    enable_telemetry: bool = False
    service_name: str = "hermes"
    collection_mode: str = "full"
    debug: bool = False
    dynamic_username: bool = False
    dynamic_uid: bool = False
    otel_exporter: OtelExporterConfig = field(default_factory=OtelExporterConfig)
    traces_config: SignalEndpointConfig = field(default_factory=SignalEndpointConfig)
    metrics_config: SignalEndpointConfig = field(default_factory=SignalEndpointConfig)
    events_config: SignalEndpointConfig = field(default_factory=SignalEndpointConfig)
    metric_export_interval: int = 60_000
    logs_export_interval: int = 5_000
    log_dir: str = ""
    ant: AntIdentity = field(default_factory=AntIdentity, repr=False)
    config_path: str = ""

    @property
    def enabled(self) -> bool:
        return self.enable_telemetry

    @property
    def traces_enabled(self) -> bool:
        return self.enable_telemetry and self.traces_config.enabled

    @property
    def metrics_enabled(self) -> bool:
        return self.enable_telemetry and self.metrics_config.enabled

    @property
    def events_enabled(self) -> bool:
        return self.enable_telemetry and self.events_config.enabled

    @classmethod
    def from_env(cls) -> "ObservHermesConfig":
        return load_config()


def _build_config(
    edition: Mapping[str, Any],
    user_config: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> ObservHermesConfig:
    env = os.environ if environ is None else environ
    validated_edition = validate_public_config(dict(edition), "edition")
    validated_user = validate_public_config(dict(user_config or {}), "user config")
    merged = _deep_merge(DEFAULT_CONFIG, validated_edition)
    merged = _deep_merge(merged, validated_user)
    _apply_environment(merged, env)
    validate_public_config(merged, "resolved config")

    exporter = merged["otelExporter"]
    resource = dict(exporter["resourceAttributes"])
    attributes_map = dict(exporter["attributes"])
    identity = AntIdentity(
        username=attributes_map.get("ant.username", ""),
        uid=attributes_map.get("ant.uid", ""),
        agent_type=resource.get("ant.agent.type", "hermes") or "hermes",
        agent_product=resource.get("ant.agent.product", "Hermes") or "Hermes",
        agent_instance=resource.get("ant.agent.instance", "") or _hostname(),
        dynamic_username=bool(merged["dynamicUsername"]),
        dynamic_uid=bool(merged["dynamicUid"]),
        agent_env=resource.get("ant.agent.env", "Default") or "Default",
        agent_scene=resource.get("ant.agent.scene", "default") or "default",
    )

    def signal(name: str) -> SignalEndpointConfig:
        value = merged[name]
        return SignalEndpointConfig(
            endpoint=value["endpoint"],
            headers=dict(value["headers"]),
            enabled=value["enabled"],
        )

    return ObservHermesConfig(
        enable_telemetry=merged["enableTelemetry"],
        service_name=merged["serviceName"],
        collection_mode=merged["collectionMode"],
        debug=merged["debug"],
        dynamic_username=merged["dynamicUsername"],
        dynamic_uid=merged["dynamicUid"],
        otel_exporter=OtelExporterConfig(
            endpoint=exporter["endpoint"],
            protocol=exporter["protocol"],
            headers=dict(exporter["headers"]),
            attributes=attributes_map,
            resource_attributes=resource,
            metrics_temporality_preference=exporter["metricsTemporalityPreference"],
        ),
        traces_config=signal("tracesConfig"),
        metrics_config=signal("metricsConfig"),
        events_config=signal("eventsConfig"),
        metric_export_interval=merged["metricExportInterval"],
        logs_export_interval=merged["logsExportInterval"],
        log_dir=merged["logDir"],
        ant=identity,
        config_path=str(config_path) if config_path else "",
    )


def load_config(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    edition_name: str | None = None,
    configs_dir: Path | None = None,
) -> ObservHermesConfig:
    env = os.environ if environ is None else environ
    path = production_config_path(environ=env, home=home)
    resolved_edition = _resolve_edition_name(edition_name, env)
    edition = _load_edition_config(
        resolved_edition,
        environ=env,
        configs_dir=configs_dir,
    )
    user = load_json_config(path)
    config = _build_config(edition, user, environ=env, config_path=path)
    if not config.log_dir:
        config.log_dir = str(default_log_dir(resolved_edition, env, home))
    return config


def config_to_public_dict(config: ObservHermesConfig) -> dict[str, Any]:
    """Serialize only fields allowed by the shared production schema."""
    return {
        "enableTelemetry": config.enable_telemetry,
        "serviceName": config.service_name,
        "collectionMode": config.collection_mode,
        "debug": config.debug,
        "dynamicUsername": config.dynamic_username,
        "dynamicUid": config.dynamic_uid,
        "otelExporter": {
            "endpoint": config.otel_exporter.endpoint,
            "protocol": config.otel_exporter.protocol,
            "headers": dict(config.otel_exporter.headers),
            "attributes": dict(config.otel_exporter.attributes),
            "resourceAttributes": dict(config.otel_exporter.resource_attributes),
            "metricsTemporalityPreference": (
                config.otel_exporter.metrics_temporality_preference
            ),
        },
        "tracesConfig": {
            "endpoint": config.traces_config.endpoint,
            "headers": dict(config.traces_config.headers),
            "enabled": config.traces_config.enabled,
        },
        "metricsConfig": {
            "endpoint": config.metrics_config.endpoint,
            "headers": dict(config.metrics_config.headers),
            "enabled": config.metrics_config.enabled,
        },
        "eventsConfig": {
            "endpoint": config.events_config.endpoint,
            "headers": dict(config.events_config.headers),
            "enabled": config.events_config.enabled,
        },
        "metricExportInterval": config.metric_export_interval,
        "logsExportInterval": config.logs_export_interval,
        "logDir": config.log_dir,
    }

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERMES_ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = HERMES_ROOT / "observ_hermes" / "configs"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


config = load_module(
    "observ_hermes_security_config_test", HERMES_ROOT / "observ_hermes/config.py"
)


class EditionConfigSecurityTests(unittest.TestCase):
    _SENSITIVE_KEYS = {
        "authorization",
        "proxy-authorization",
        "api-key",
        "x-api-key",
        "token",
        "secret",
        "password",
    }

    def test_tracked_edition_configs_contain_no_embedded_credentials(self) -> None:
        findings: list[str] = []

        def inspect(value: object, location: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = str(key).strip().lower().replace("_", "-")
                    if normalized in self._SENSITIVE_KEYS:
                        findings.append(f"{location}.{key}: sensitive key")
                    inspect(child, f"{location}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    inspect(child, f"{location}[{index}]")
            elif isinstance(value, str) and value.strip().lower().startswith(
                ("basic ", "bearer ")
            ):
                findings.append(f"{location}: embedded authorization value")

        for path in sorted(CONFIGS_DIR.glob("*.json")):
            inspect(json.loads(path.read_text()), path.name)

        self.assertEqual(findings, [])

    def test_runtime_signal_header_override_is_supported_but_redacted_from_repr(
        self,
    ) -> None:
        edition = json.loads((CONFIGS_DIR / "aivision.json").read_text())
        secret = "Bearer runtime-only-secret"
        with patch.dict(
            os.environ,
            {"OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"Authorization={secret}"},
            clear=True,
        ):
            runtime = config._build_config(edition)

        self.assertEqual(runtime.traces_config.headers["Authorization"], secret)
        self.assertEqual(runtime.traces_config.headers, {"Authorization": secret})
        self.assertNotIn(secret, repr(runtime))
        self.assertNotIn("Authorization", repr(runtime.traces_config))

    def test_runtime_base_headers_are_redacted_from_repr(self) -> None:
        secret = "runtime-api-key"
        with patch.dict(
            os.environ,
            {"OTEL_EXPORTER_OTLP_HEADERS": f"x-api-key={secret}"},
            clear=True,
        ):
            runtime = config._build_config({})

        self.assertEqual(runtime.otel_exporter.headers, {"x-api-key": secret})
        self.assertNotIn(secret, repr(runtime))
        self.assertNotIn("x-api-key", repr(runtime.otel_exporter))


if __name__ == "__main__":
    unittest.main()

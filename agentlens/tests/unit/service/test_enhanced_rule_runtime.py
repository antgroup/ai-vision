import hashlib
import json

import pytest

from agentlens.domain.sandbox import SandboxError
from agentlens.service.rule_runtime import (
    normalize_rule_runtime,
    qualified_runtime_manifests,
    require_rule_runtime,
)


def manifest():
    value = {
        "id": "python-eval-v1",
        "python_version": "3.11.14",
        "packages": {
            name: "1.0"
            for name in ("pytest", "numpy", "pandas", "pydantic", "jsonschema", "openpyxl")
        },
    }
    value["manifest_sha256"] = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


def test_materials_are_normalized_and_detached():
    source = {
        "rule_runtime": "python-eval-v1",
        "rule_runtime_manifest": manifest()["manifest_sha256"],
        "materials": {"files": [{"path": "a.txt", "encoding": "utf-8", "content": "hello"}]},
    }
    normalized = normalize_rule_runtime(source)
    source["materials"]["files"][0]["content"] = "changed"
    assert normalized["materials"]["files"][0]["content"] == "hello"
    assert normalized["materials"]["files"][0]["sha256"] == hashlib.sha256(b"hello").hexdigest()


@pytest.mark.parametrize(
    "spec",
    [
        {"materials": {}},
        {"rule_runtime": "unknown"},
        {"rule_runtime": "python-eval-v1", "rule_runtime_manifest": "bad"},
    ],
)
def test_unversioned_or_unknown_runtime_rejected(spec):
    with pytest.raises(ValueError):
        normalize_rule_runtime(spec)


def test_qualified_image_and_manifest_required():
    image = "worker@sha256:" + "a" * 64
    health = {
        "ready": True,
        "capabilities": ["custom_rule", "python_eval_v1"],
        "runtime_manifests": [{**manifest(), "image": image}],
    }
    assert qualified_runtime_manifests(health, image) == health["runtime_manifests"]
    spec = {
        "rule_runtime": "python-eval-v1",
        "rule_runtime_manifest": manifest()["manifest_sha256"],
    }
    require_rule_runtime(spec, {"mode": "sandbox", "image": image}, health)
    for profile, changed in [
        ({"mode": "local"}, health),
        ({"mode": "sandbox", "image": "other"}, health),
        ({"mode": "sandbox", "image": image}, {**health, "capabilities": ["custom_rule"]}),
    ]:
        with pytest.raises(SandboxError):
            require_rule_runtime(spec, profile, changed)


def test_public_rule_validation_normalizes_materials_and_preserves_legacy():
    from agentlens.domain.enums import DiagnoserMode, DiagnoserType
    from agentlens.domain.errors import Validation
    from agentlens.web.routes.diagnoser import _validate_executor_spec

    code = (
        "import json\ndef evaluate(trace, context):\n"
        '    return {"metrics": [{"name": "q", "verdict": "good"}]}'
    )
    spec = {
        "code": code,
        "metrics": [{"name": "q"}],
        "rule_runtime": "python-eval-v1",
        "rule_runtime_manifest": manifest()["manifest_sha256"],
        "materials": {"files": [{"path": "data.csv", "encoding": "utf-8", "content": "a,b\n1,2"}]},
    }
    result = _validate_executor_spec(spec, DiagnoserType.CUSTOM, DiagnoserMode.RULE)
    assert result["materials"]["files"][0]["sha256"]
    assert "sha256" not in spec["materials"]["files"][0]
    with pytest.raises(Validation):
        _validate_executor_spec(
            {"code": code, "metrics": [{"name": "q"}]}, DiagnoserType.CUSTOM, DiagnoserMode.RULE
        )
    with pytest.raises(Validation):
        _validate_executor_spec(
            {
                **spec,
                "materials": {"files": [{"path": "../bad", "encoding": "utf-8", "content": "x"}]},
            },
            DiagnoserType.CUSTOM,
            DiagnoserMode.RULE,
        )


def test_enhanced_budget_keeps_separate_five_second_legacy_default(monkeypatch):
    from pydantic import ValidationError

    from agentlens.config import Settings

    for key in ("AGENTLENS_TOKEN", "SIGNOZ_OB_URL", "SIGNOZ_ORG_ID", "ANTCOLLECTOR_ENDPOINT"):
        monkeypatch.setenv(key, "fixture")
    settings = Settings(_env_file=None)
    assert settings.exec_timeout == 5
    assert settings.enhanced_exec_timeout == 30
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ENHANCED_EXEC_TIMEOUT=301)


@pytest.mark.parametrize(
    "code",
    [
        "rule_test_harness_error",
        "rule_test_incomplete",
        "rule_timeout",
        "rule_output_too_large",
        "rule_runtime_unavailable",
    ],
)
def test_worker_runtime_failures_keep_safe_ownership_codes(code):
    from agentlens.service.sandbox_execution import _worker_result

    response = {
        "exit_code": 0,
        "stdout": json.dumps(
            {"status": "error", "failure_code": code, "reason": "private user content"}
        ),
        "stderr": "",
        "timed_out": False,
        "truncated": False,
        "cleanup_verified": False,
    }
    with pytest.raises(SandboxError) as error:
        _worker_result(response, expected_manifest="a" * 64)
    assert error.value.code == code
    assert "private user content" not in str(error.value)


def test_success_requires_returned_manifest_and_preserves_bounded_capture():
    from agentlens.service.sandbox_execution import _worker_result

    worker = {
        "status": "ok",
        "result": {"metrics": []},
        "runtime_manifest": manifest(),
        "captured_stdout": "hello",
        "captured_stderr": "",
        "output_truncated": False,
    }
    response = {
        "exit_code": 0,
        "stdout": json.dumps(worker),
        "stderr": "",
        "timed_out": False,
        "truncated": False,
        "cleanup_verified": False,
    }
    result = _worker_result(response, expected_manifest=manifest()["manifest_sha256"])
    assert result["_sandbox_execution"]["captured_stdout"] == "hello"
    with pytest.raises(SandboxError) as error:
        _worker_result(response, expected_manifest="b" * 64)
    assert error.value.code == "sandbox_runtime_unavailable"


@pytest.mark.parametrize(
    "first_import",
    [
        "from agentlens.service.rule_runtime import normalize_rule_runtime",
        "from agentlens.diagnoser.executors.rule.custom.enhanced_contract import RUNTIME_ID",
        "from agentlens.diagnoser.executors.rule.custom.executor import RuleCustomExecutor",
    ],
)
def test_runtime_service_imports_in_fresh_process_without_preloaded_diagnosers(first_import):
    import subprocess
    import sys

    initial_spec = {
        "code": "def evaluate(trace):\n    return {}",
        "metrics": [{"name": "quality"}],
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            first_import
            + "; from agentlens.service.rule_runtime import normalize_rule_runtime; "
            + "assert normalize_rule_runtime({}) == {}; "
            + "from agentlens.diagnoser.executors.rule.custom.executor import RuleCustomExecutor; "
            + f"RuleCustomExecutor({initial_spec!r})",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr

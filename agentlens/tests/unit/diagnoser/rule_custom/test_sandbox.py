import pytest

from agentlens.diagnoser.executors.rule.custom.sandbox import SandboxError, execute_one_shot
from agentlens.diagnoser.executors.rule.custom.validator import validate_code

VALID_CODE = """
def evaluate(trace):
    output = trace.get("agent_output", "")
    if not output:
        return {"metrics": [
            {"name": "quality", "verdict": "bad", "reason": "no output", "evidence": {}}
        ]}
    return {"metrics": [
        {"name": "quality", "verdict": "good", "reason": "has output", "evidence": {}}
    ]}
"""

NOOP_CODE = """
def evaluate(trace):
    if not trace.get("steps"):
        return {"metrics": [
            {"name": "quality", "verdict": "good", "reason": "no steps", "evidence": {}}
        ]}
    return {"metrics": [{"name": "quality", "verdict": "good", "reason": "ok", "evidence": {}}]}
"""

JSON_CODE = """
def evaluate(trace):
    output = trace.get("agent_output", "")
    try:
        json.loads(output)
    except (TypeError, ValueError):
        return {"metrics": [
            {"name": "quality", "verdict": "bad", "reason": "not JSON", "evidence": {}}
        ]}
    return {"metrics": [
        {"name": "quality", "verdict": "good", "reason": "valid JSON", "evidence": {}}
    ]}
"""

RE_CODE = """
def evaluate(trace):
    output = trace.get("agent_output", "")
    if re.search(r"error|exception", output, re.IGNORECASE):
        return {"metrics": [
            {"name": "quality", "verdict": "bad", "reason": "contains error", "evidence": {}}
        ]}
    return {"metrics": [{"name": "quality", "verdict": "good", "reason": "clean", "evidence": {}}]}
"""

RUNTIME_ERROR_CODE = """
def evaluate(trace):
    return 1 / 0
"""

TIMEOUT_CODE = """
def evaluate(trace):
    i = 0
    while True:
        i += 1
"""


def _run(code: str, trace: dict, timeout: float = 5.0) -> dict:
    compiled_code = validate_code(code)
    return execute_one_shot(compiled_code, trace, timeout_seconds=timeout, code=code)


class TestSuccessfulExecution:
    def test_evaluated_result(self):
        result = _run(VALID_CODE, {"agent_output": "hello"})
        assert result["metrics"][0]["verdict"] == "good"

    def test_evaluated_no_output(self):
        result = _run(VALID_CODE, {"agent_output": ""})
        assert result["metrics"][0]["verdict"] == "bad"

    def test_noop_result(self):
        result = _run(NOOP_CODE, {})
        assert result["metrics"][0]["verdict"] == "good"
        assert "no steps" in result["metrics"][0]["reason"]

    def test_json_builtin_available(self):
        result = _run(JSON_CODE, {"agent_output": '{"key": "value"}'})
        assert result["metrics"][0]["verdict"] == "good"

    def test_json_invalid(self):
        result = _run(JSON_CODE, {"agent_output": "not json"})
        assert result["metrics"][0]["verdict"] == "bad"

    def test_re_builtin_available(self):
        result = _run(RE_CODE, {"agent_output": "all good"})
        assert result["metrics"][0]["verdict"] == "good"

    def test_re_matches_error(self):
        result = _run(RE_CODE, {"agent_output": "RuntimeError occurred"})
        assert result["metrics"][0]["verdict"] == "bad"


class TestErrorHandling:
    def test_unsupported_input_is_not_silently_stringified(self):
        with pytest.raises(TypeError, match="not JSON serializable"):
            _run(VALID_CODE, {"agent_output": object()})

    def test_runtime_error(self):
        with pytest.raises(SandboxError, match="ZeroDivisionError"):
            _run(RUNTIME_ERROR_CODE, {})

    def test_timeout(self):
        with pytest.raises(SandboxError, match="超时") as exc_info:
            _run(TIMEOUT_CODE, {}, timeout=1.0)
        assert exc_info.value.failure_code == "exec_timeout"

    def test_sandbox_error_has_failure_code(self):
        with pytest.raises(SandboxError) as exc_info:
            _run(RUNTIME_ERROR_CODE, {})
        assert exc_info.value.failure_code == "rule_runtime_error"


class TestSubprocessIsolation:
    def test_import_blocked_at_runtime(self):
        code = """
def evaluate(trace):
    try:
        import os
        return {"metrics": [
            {"name": "quality", "verdict": "bad", "reason": "should not reach", "evidence": {}}
        ]}
    except ImportError:
        return {"metrics": [
            {"name": "quality", "verdict": "good", "reason": "import blocked", "evidence": {}}
        ]}
"""
        # AST validator catches import, so compile directly for this test
        compiled_code = compile(code, "<test>", "exec")
        with pytest.raises(SandboxError):
            execute_one_shot(compiled_code, {}, code=code)

    def test_print_is_noop(self):
        code = """
def evaluate(trace):
    print("hello")
    return {"metrics": [{"name": "quality", "verdict": "good", "reason": "ok", "evidence": {}}]}
"""
        result = _run(code, {})
        assert result["metrics"][0]["verdict"] == "good"

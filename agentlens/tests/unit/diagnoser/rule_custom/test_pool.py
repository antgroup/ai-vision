import pytest

from agentlens.diagnoser.executors.rule.custom.pool import CustomRuleWorkerPool, code_hash
from agentlens.diagnoser.executors.rule.custom.sandbox import SandboxError

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

ERROR_CODE = """
def evaluate(trace):
    return 1 / 0
"""


@pytest.fixture
def pool():
    p = CustomRuleWorkerPool(size=1)
    p.start()
    yield p
    p.stop()


class TestPoolLifecycle:
    def test_start_stop(self):
        p = CustomRuleWorkerPool(size=1)
        p.start()
        assert p._started
        p.stop()
        assert not p._started

    def test_double_start_idempotent(self):
        p = CustomRuleWorkerPool(size=1)
        p.start()
        p.start()
        assert len(p._workers) == 1
        p.stop()

    def test_double_stop_safe(self):
        p = CustomRuleWorkerPool(size=1)
        p.start()
        p.stop()
        p.stop()


class TestPoolExecution:
    def test_successful_execution(self, pool):
        h = code_hash(VALID_CODE)
        result, _ = pool.execute(h, VALID_CODE, {"agent_output": "hello"})
        assert result["metrics"][0]["verdict"] == "good"
        assert result["metrics"][0]["reason"] == "has output"

    def test_no_output_verdict(self, pool):
        h = code_hash(VALID_CODE)
        result, _ = pool.execute(h, VALID_CODE, {"agent_output": ""})
        assert result["metrics"][0]["verdict"] == "bad"

    def test_runtime_error(self, pool):
        h = code_hash(ERROR_CODE)
        with pytest.raises(SandboxError, match="ZeroDivisionError"):
            pool.execute(h, ERROR_CODE, {})

    def test_cache_reuses_compiled_code(self, pool):
        h = code_hash(VALID_CODE)
        pool.execute(h, VALID_CODE, {"agent_output": "a"})  # warm cache
        result, _ = pool.execute(h, VALID_CODE, {"agent_output": "b"})
        assert result["metrics"][0]["verdict"] == "good"


class TestPoolRecovery:
    def test_worker_replaced_after_crash(self, pool):
        pool._workers[0].stop()
        h = code_hash(VALID_CODE)
        result, _ = pool.execute(h, VALID_CODE, {"agent_output": "hello"})
        assert result["metrics"][0]["verdict"] == "good"


class TestCodeHash:
    def test_deterministic(self):
        h1 = code_hash("def evaluate(trace): pass")
        h2 = code_hash("def evaluate(trace): pass")
        assert h1 == h2

    def test_different_code_different_hash(self):
        h1 = code_hash("def evaluate(trace): pass")
        h2 = code_hash("def evaluate(trace): return {}")
        assert h1 != h2

    def test_hash_length(self):
        h = code_hash("x")
        assert len(h) == 64


class TestFallback:
    def test_fallback_when_not_started(self):
        p = CustomRuleWorkerPool(size=1)
        h = code_hash(VALID_CODE)
        result, _ = p.execute(h, VALID_CODE, {"agent_output": "hello"})
        assert result["metrics"][0]["verdict"] == "good"

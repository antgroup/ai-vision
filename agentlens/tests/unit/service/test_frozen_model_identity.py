"""Frozen plans may rotate keys only within the original provider/model identity."""

import json

import httpx
import pytest

from agentlens.diagnoser.executors.llm import base
from agentlens.domain.enums import ModelConnectionStatus
from tests.unit.service.test_core_thinking_defaults import new_quick_snapshot, runtime_spec


@pytest.mark.parametrize("trusted", [True, False])
@pytest.mark.parametrize(
    "change", ["endpoint", "path", "model_name", "missing_frozen", "key", "trailing_slash"]
)
def test_snapshot_runner_judge_rejects_identity_drift(monkeypatch, trusted, change):
    _, snapshot, connection = new_quick_snapshot(monkeypatch)
    if not trusted:
        snapshot.pop("execution_policy")
        connection.status = ModelConnectionStatus.AVAILABLE
    connection.api_key = "synthetic-rotated-key"
    if change == "endpoint":
        connection.endpoint = "https://replacement.invalid/v1"
    elif change == "path":
        connection.endpoint += "/different-tenant"
    elif change == "model_name":
        connection.model_name = "replacement-model"
    elif change == "missing_frozen":
        snapshot["diagnosers"][0]["model_connection"] = None
    elif change == "trailing_slash":
        connection.endpoint += "/"
    captured = []

    def respond(request):
        captured.append((str(request.url), request.headers["authorization"]))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "metrics": [
                                        {
                                            "name": "quality",
                                            "verdict": "good",
                                            "reason": "synthetic",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    original = base.LLMJudge
    monkeypatch.setattr(
        base,
        "LLMJudge",
        lambda *a, **kw: original(*a, **kw, transport=httpx.MockTransport(respond)),
    )
    spec = runtime_spec(snapshot, connection)
    if change not in {"key", "trailing_slash"}:
        assert "model" not in spec
        assert "synthetic-rotated-key" not in json.dumps(spec)
        with pytest.raises(base.JudgeUnavailable, match="模型连接未配置或不可用"):
            base._build_judge(spec)
        assert captured == []
    else:
        judge = base._build_judge(spec)
        try:
            judge.judge("synthetic evidence")
        finally:
            judge.close()
        assert captured == [
            ("https://standard-chat.test/v1/chat/completions", "Bearer synthetic-rotated-key")
        ]

"""Official Anthropic compatibility stays isolated from custom providers."""

import json

import httpx
import pytest

from agentlens.infra.llm_judge.client import LLMJudge


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.anthropic.com/v1",
        "https://api.anthropic.com/v1/",
        " https://api.anthropic.com/v1/ ",
    ],
)
def test_anthropic_probe_and_judge_omit_unsupported_sampling_controls(endpoint):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"metrics":[{"name":"quality","verdict":"good","reason":"ok"}]}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            },
        )

    judge = LLMJudge(
        endpoint,
        "private-key",
        "claude-fixture",
        temperature=0,
        enable_thinking=True,
        reasoning_effort="high",
        max_tokens=512,
        max_retries=0,
        transport=httpx.MockTransport(respond),
    )
    try:
        judge.probe()
        result = judge.judge("Evaluate quality")
    finally:
        judge.close()
    assert result["metrics"][0]["reason"] == "ok"
    assert len(requests) == 2
    probe, evaluation = [json.loads(request.content) for request in requests]
    for request, payload in zip(requests, (probe, evaluation), strict=True):
        assert str(request.url) == "https://api.anthropic.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer private-key"
        assert "temperature" not in payload
        assert "enable_thinking" not in payload
        assert "chat_template_kwargs" not in payload
        assert "reasoning_effort" not in payload
        assert payload["model"] == "claude-fixture"
    assert probe["max_tokens"] == 1
    assert evaluation["max_tokens"] == 512
    assert evaluation["messages"][0]["role"] == "system"
    assert "Evaluate quality" in evaluation["messages"][1]["content"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.openai.com/v1",
        "https://api.anthropic.com.evil.test/v1",
        "https://api.anthropic.com/v1/proxy",
        "http://api.anthropic.com/v1",
    ],
)
def test_other_endpoints_preserve_existing_generation_parameters(endpoint):
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"metrics":[{"name":"quality","verdict":"good","reason":"ok"}]}'
                            )
                        }
                    }
                ]
            },
        )

    judge = LLMJudge(
        endpoint,
        "private-key",
        "fixture",
        temperature=0.3,
        reasoning_effort="high",
        max_tokens=512,
        max_retries=0,
        transport=httpx.MockTransport(respond),
    )
    try:
        judge.probe()
        judge.judge("Evaluate quality")
    finally:
        judge.close()
    assert payloads[0]["temperature"] == 0
    assert payloads[1]["temperature"] == 0.3
    assert payloads[1]["reasoning_effort"] == "high"
    for payload in payloads:
        assert payload["enable_thinking"] is False
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}

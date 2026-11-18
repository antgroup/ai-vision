"""N0：LLMJudge —— 正常打分 / <think> 剥离 / 429 重试 / 解析失败降级。"""

import json

import httpx
import pytest

from agentlens.diagnoser.executors.llm.base import _build_judge
from agentlens.infra.llm_judge import JudgeUnavailable, LLMJudge


def _judge(handler, max_retries=0, enable_thinking=False, temperature=0):
    return LLMJudge(
        "http://judge.test/v1",
        "k",
        "M",
        max_retries=max_retries,
        backoff_sec=0,
        enable_thinking=enable_thinking,
        temperature=temperature,
        transport=httpx.MockTransport(handler),
    )


def _chat(content: str, status=200):
    return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})


def test_enable_thinking_false_is_sent_when_configured():
    captured = {}

    def handler(req):
        captured["payload"] = json.loads(req.content)
        return _chat(
            '{"metrics": [{"name": "quality", "verdict": "good", '
            '"reason": "未命中", "evidence": {}}]}'
        )

    j = _judge(handler, enable_thinking=False)
    j.judge("是否命中")

    assert captured["payload"]["enable_thinking"] is False
    assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_enable_thinking_true_is_sent_in_both_forms():
    captured = {}

    def handler(req):
        captured["payload"] = json.loads(req.content)
        return _chat(
            '{"metrics": [{"name": "quality", "verdict": "good", '
            '"reason": "未命中", "evidence": {}}]}'
        )

    j = _judge(handler, enable_thinking=True)
    j.judge("是否命中")

    assert captured["payload"]["enable_thinking"] is True
    assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": True}


def test_configured_temperature_is_sent():
    captured = {}

    def handler(req):
        captured["payload"] = json.loads(req.content)
        return _chat(
            '{"metrics": [{"name": "quality", "verdict": "good", '
            '"reason": "未命中", "evidence": {}}]}'
        )

    j = _judge(handler, temperature=0.1)
    j.judge("是否命中")

    assert captured["payload"]["temperature"] == 0.1


def test_enable_thinking_is_omitted_when_explicitly_disabled_for_transport():
    captured = {}

    def handler(req):
        captured["payload"] = json.loads(req.content)
        return _chat(
            '{"metrics": [{"name": "quality", "verdict": "good", '
            '"reason": "未命中", "evidence": {}}]}'
        )

    j = _judge(handler, enable_thinking=None)
    j.judge("是否命中")

    assert "enable_thinking" not in captured["payload"]
    assert "chat_template_kwargs" not in captured["payload"]


def test_probe_sends_both_thinking_switch_forms():
    captured = {}

    def handler(req):
        captured["payload"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    j = _judge(handler, enable_thinking=False)
    j.probe()

    assert captured["payload"]["enable_thinking"] is False
    assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("effort", ["low", 1, 100])
def test_build_judge_reads_runtime_sampling_from_spec(effort):
    judge = _build_judge(
        {
            "enable_thinking": True,
            "temperature": 0.2,
            "reasoning_effort": effort,
            "max_tokens": 1024,
            "model": {
                "endpoint": "http://judge.test/v1",
                "api_key": "key",
                "model_id": "model",
            },
        }
    )
    try:
        assert judge.enable_thinking is True
        assert judge.temperature == 0.2
        assert judge.reasoning_effort == effort
        assert type(judge.reasoning_effort) is type(effort)
        assert judge.max_tokens == 1024
    finally:
        judge.close()


@pytest.mark.parametrize("value", [0, 1, 1.0, "false", [], {}])
def test_build_judge_rejects_invalid_thinking_values(value):
    with pytest.raises(JudgeUnavailable, match="enable_thinking"):
        _build_judge(
            {
                "enable_thinking": value,
                "model": {"endpoint": "http://judge.test/v1", "api_key": "key", "model_id": "m"},
            }
        )


@pytest.mark.parametrize("configured_timeout", [None, 0.1, 180, 300])
def test_spec_timeout_configures_http_transport_without_changing_judge_payload(
    monkeypatch, configured_timeout
):
    from agentlens.diagnoser.executors.llm import base

    requests = []

    def handler(request):
        requests.append((request.extensions["timeout"], json.loads(request.content)))
        return _chat(
            '{"metrics":[{"name":"quality","verdict":"good","reason":"ok","evidence":{}}]}'
        )

    monkeypatch.setattr(
        base,
        "LLMJudge",
        lambda *args, **kwargs: LLMJudge(*args, **kwargs, transport=httpx.MockTransport(handler)),
    )
    spec = {"model": {"endpoint": "http://judge.test/v1", "api_key": "key", "model_id": "m"}}
    if configured_timeout is not None:
        spec["request_timeout_sec"] = configured_timeout
    judge = _build_judge(spec)
    try:
        judge.judge("Keep the original prompt")
    finally:
        judge.close()
    expected = configured_timeout if configured_timeout is not None else 60
    assert requests[0][0] == dict.fromkeys(("connect", "read", "write", "pool"), expected)
    assert "request_timeout_sec" not in requests[0][1]
    assert "timeout" not in requests[0][1]
    assert requests[0][1]["messages"][1]["content"] == "## 评判任务\nKeep the original prompt"


@pytest.mark.parametrize("effort", ["low", 1, 50, 100])
def test_optional_generation_limits_are_forwarded_and_omitted_by_default(effort):
    requests = []

    def handler(req):
        requests.append(json.loads(req.content))
        return _chat(
            '{"metrics":[{"name":"quality","verdict":"good","reason":"ok","evidence":{}}]}'
        )

    for options in ({}, {"reasoning_effort": effort, "max_tokens": 1024}):
        judge = LLMJudge(
            "http://judge.test/v1",
            "test-key",
            "test-model",
            max_retries=0,
            transport=httpx.MockTransport(handler),
            **options,
        )
        try:
            judge.judge("Keep the actual prompt")
        finally:
            judge.close()

    assert "reasoning_effort" not in requests[0]
    assert "max_tokens" not in requests[0]
    assert requests[1]["reasoning_effort"] == effort
    assert type(requests[1]["reasoning_effort"]) is type(effort)
    assert requests[1]["max_tokens"] == 1024
    assert requests[0]["messages"] == requests[1]["messages"]
    assert requests[0]["enable_thinking"] is False
    assert requests[1]["enable_thinking"] is False


@pytest.mark.parametrize(
    "options",
    [
        {"reasoning_effort": True},
        {"reasoning_effort": False},
        {"reasoning_effort": 0},
        {"reasoning_effort": 101},
        {"reasoning_effort": -1},
        {"reasoning_effort": 1.0},
        {"reasoning_effort": "1"},
        {"reasoning_effort": ""},
        {"reasoning_effort": "invalid"},
        {"max_tokens": True},
        {"max_tokens": "1024"},
        {"max_tokens": 0},
        {"max_tokens": -1},
        {"max_tokens": 1.5},
    ],
)
def test_invalid_generation_limits_are_rejected_before_client_requests(options):
    with pytest.raises(ValueError):
        LLMJudge("http://judge.test/v1", "test-key", "test-model", **options)


@pytest.mark.parametrize(
    "options",
    [
        {"reasoning_effort": None},
        {"request_timeout_sec": True},
        {"request_timeout_sec": None},
        {"request_timeout_sec": 0},
        {"request_timeout_sec": -1},
        {"request_timeout_sec": 300.1},
        {"request_timeout_sec": float("nan")},
        {"request_timeout_sec": float("inf")},
        {"request_timeout_sec": "180"},
        {"reasoning_effort": True},
        {"reasoning_effort": 0},
        {"reasoning_effort": 101},
        {"reasoning_effort": 1.0},
        {"reasoning_effort": "invalid"},
        {"max_tokens": True},
        {"max_tokens": 0},
    ],
)
def test_corrupt_persisted_generation_limits_fail_before_judge_is_built(options):
    with pytest.raises(JudgeUnavailable):
        _build_judge(
            {
                **options,
                "model": {
                    "endpoint": "http://judge.test/v1",
                    "api_key": "test-key",
                    "model_id": "test-model",
                },
            }
        )


def test_metrics_ok():
    j = _judge(
        lambda req: _chat(
            '{"metrics": [{"name": "quality", "verdict": "good", '
            '"reason": "达成目标", "evidence": {}}]}'
        )
    )

    out = j.judge("打分")

    assert out == {
        "metrics": [
            {
                "name": "quality",
                "verdict": "good",
                "reason": "达成目标",
                "evidence": {},
            }
        ],
    }


def test_metrics_protocol_is_in_system_message():
    captured = {}

    def handler(req):
        captured["payload"] = json.loads(req.content)
        return _chat(
            '{"metrics": [{"name": "quality", "verdict": "good", '
            '"reason": "达标", "evidence": {}}]}'
        )

    j = _judge(handler)
    j.judge("评估 quality")

    messages = captured["payload"]["messages"]
    assert '"metrics": [{"name": "任务中声明的 metric key"' in messages[0]["content"]
    assert "verdict 只能是 good 或 bad" in messages[0]["content"]
    assert "无论 verdict 为 good 还是 bad，都必须提供非空 reason" in messages[0]["content"]
    assert "reason 必须说明支持当前 verdict 的具体判定依据" in messages[0]["content"]
    assert "evidence 可省略" in messages[0]["content"]
    assert messages[1]["content"] == "## 评判任务\n评估 quality"


def test_multimodal_request_uses_text_then_image_url_parts():
    captured = {}

    def handler(req):
        captured["payload"] = json.loads(req.content)
        return _chat(
            '{"metrics": [{"name": "quality", "verdict": "good", "reason": "图片回答与问题相关"}]}'
        )

    judge = _judge(handler)
    judge.judge(
        "评估图片",
        image_urls=[
            "https://media.example.com/a.png",
            "https://media.example.com/b.png",
        ],
    )

    content = captured["payload"]["messages"][1]["content"]
    assert content == [
        {"type": "text", "text": "## 评判任务\n评估图片"},
        {
            "type": "image_url",
            "image_url": {"url": "https://media.example.com/a.png"},
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://media.example.com/b.png"},
        },
    ]


def test_metrics_rejects_invalid_verdict():
    j = _judge(
        lambda req: _chat(
            '{"metrics": [{"name": "quality", "verdict": "failed", "reason": "x", "evidence": {}}]}'
        )
    )

    with pytest.raises(JudgeUnavailable):
        j.judge("打分")


@pytest.mark.parametrize(
    "metric",
    [
        '{"name": "quality", "verdict": "good"}',
        '{"name": "quality", "verdict": "good", "reason": "  "}',
    ],
)
def test_metrics_rejects_good_without_non_empty_reason(metric):
    j = _judge(lambda req: _chat(f'{{"metrics": [{metric}]}}'))

    with pytest.raises(JudgeUnavailable):
        j.judge("判断")


def test_metrics_retries_good_without_reason():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return _chat('{"metrics": [{"name": "quality", "verdict": "good"}]}')
        return _chat(
            '{"metrics": [{"name": "quality", "verdict": "good", "reason": "未发现质量问题"}]}'
        )

    result = _judge(handler, max_retries=1).judge("判断")

    assert calls["n"] == 2
    assert result["metrics"][0]["reason"] == "未发现质量问题"


def test_metrics_rejects_bad_without_reason():
    j = _judge(lambda req: _chat('{"metrics": [{"name": "quality", "verdict": "bad"}]}'))

    with pytest.raises(JudgeUnavailable):
        j.judge("打分")


def test_metrics_rejects_non_object_evidence():
    j = _judge(
        lambda req: _chat(
            '{"metrics": [{"name": "quality", "verdict": "bad", "reason": "x", "evidence": []}]}'
        )
    )

    with pytest.raises(JudgeUnavailable):
        j.judge("打分")


def test_strips_think_block():
    j = _judge(
        lambda req: _chat(
            '<think>纠结一下</think>\n{"metrics": [{"name": "quality", '
            '"verdict": "good", "reason": "未达成", "evidence": {}}]}'
        )
    )
    out = j.judge("p")
    assert out["metrics"][0]["reason"] == "未达成"


def test_accepts_content_parts():
    j = _judge(
        lambda req: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        '{"metrics": [{"name": "quality", '
                                        '"verdict": "good", "reason": "基本达标", '
                                        '"evidence": {}}]}'
                                    ),
                                }
                            ]
                        }
                    }
                ]
            },
        )
    )
    out = j.judge("p")
    assert out["metrics"][0]["reason"] == "基本达标"


def test_unparseable_response_includes_safe_shape_detail():
    j = _judge(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": None}}]}))
    with pytest.raises(JudgeUnavailable, match="message.content type=NoneType"):
        j.judge("p")


def test_429_retries_then_unavailable():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(429, text="rate limited")

    j = _judge(h, max_retries=2)
    with pytest.raises(JudgeUnavailable):
        j.judge("p")
    assert calls["n"] == 3  # 首次 + 2 重试


def test_5xx_retries_then_unavailable():
    j = _judge(lambda req: httpx.Response(503, text="down"), max_retries=1)
    with pytest.raises(JudgeUnavailable):
        j.judge("p")


def test_4xx_returns_endpoint_error_without_retry():
    calls = {"n": 0}
    events = []

    class _Logger:
        def warning(self, event, **fields):
            events.append((event, fields))

    def handler(req):
        calls["n"] += 1
        return httpx.Response(
            400,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "code": "context_length_exceeded",
                    "message": "full upstream error detail is logged",
                }
            },
        )

    j = _judge(handler, max_retries=2)
    j._log = _Logger()
    with pytest.raises(JudgeUnavailable, match="after 1 attempt: http 400") as raised:
        j.judge("p")
    assert calls["n"] == 1
    assert "invalid_request_error" in str(raised.value)
    assert "context_length_exceeded" in str(raised.value)
    assert "full upstream error detail" not in str(raised.value)
    assert events == [
        (
            "llm_judge.unavailable",
            {
                "err": (
                    "http 400 (upstream_error_type=invalid_request_error, "
                    "upstream_error_code=context_length_exceeded)"
                ),
                "model": "M",
                "attempts": 1,
                "failure_kind": "http_error",
                "status_code": 400,
                "upstream_error_type": "invalid_request_error",
                "upstream_error_code": "context_length_exceeded",
            },
        )
    ]


def test_4xx_omits_response_body_and_request_prompt():
    events = []

    class _Logger:
        def warning(self, event, **fields):
            events.append((event, fields))

    response = {
        "code": "invalid_parameter",
        "type": "invalid_request_error",
        "message": "complete upstream response detail",
        "error": "bad request",
    }
    j = _judge(lambda req: httpx.Response(400, json=response))
    j._log = _Logger()

    with pytest.raises(JudgeUnavailable) as raised:
        j.judge("private prompt")

    fields = events[0][1]
    assert fields["upstream_error_type"] == "invalid_request_error"
    assert fields["upstream_error_code"] == "invalid_parameter"
    assert "response_body" not in fields
    assert "complete upstream response detail" not in str(fields)
    assert "private prompt" not in str(fields)
    assert "complete upstream response detail" not in str(raised.value)


def test_unparseable_response_degrades():
    # 200 但内容不含 JSON → 重试耗尽 → JudgeUnavailable
    j = _judge(lambda req: _chat("我觉得还行，没有给 JSON"), max_retries=1)
    with pytest.raises(JudgeUnavailable):
        j.judge("p")


def test_missing_field_is_unparseable():
    j = _judge(lambda req: _chat('{"metrics": []}'), max_retries=0)
    with pytest.raises(JudgeUnavailable):
        j.judge("p")


def test_request_error_retries():
    def h(req):
        raise httpx.ConnectError("boom")

    j = _judge(h, max_retries=1)
    with pytest.raises(JudgeUnavailable):
        j.judge("p")

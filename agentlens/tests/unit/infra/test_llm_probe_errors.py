"""Probe failures expose fixed reasons, never upstream bodies or credentials."""

import httpx
import pytest

from agentlens.infra.llm_judge import JudgeUnavailable, LLMJudge

_SECRET = "sk-probe-error-fixture-secret"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": {"message": _SECRET}}, "authentication_failed"),
        (403, {"error": {"message": _SECRET}}, "permission_denied"),
        (404, {"error": {"code": "function_not_found_for_account"}}, "model_not_found"),
        (404, {"error": {"code": "invalid_api_key"}}, "model_not_found"),
        (402, {}, "quota_exceeded"),
        (429, {}, "rate_limited"),
        (429, {"error": {"code": "insufficient_quota"}}, "quota_exceeded"),
        (400, {"error": {"code": "model_not_found"}}, "model_not_found"),
        (400, {"error": {"type": "invalid_request_error"}}, "invalid_request"),
        (400, {"message": "insufficient quota " + _SECRET}, "invalid_request"),
        (422, {}, "invalid_request"),
        (408, {}, "timeout"),
        (504, {}, "timeout"),
        (500, {"error": {"code": "invalid_api_key"}}, "unavailable"),
        (503, {}, "unavailable"),
        (302, {}, "unavailable"),
        (200, {"error": {"code": "insufficient_quota", "message": _SECRET}}, "quota_exceeded"),
        (200, {"error": {"code": _SECRET, "message": _SECRET}}, "unavailable"),
        (200, {"error_code": 999999, "error_msg": _SECRET}, "unavailable"),
    ],
)
def test_probe_classifies_failures_without_echoing_upstream_fields(status, body, expected):
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    judge = LLMJudge(
        "https://judge.test/v1",
        _SECRET,
        "test-model",
        max_retries=0,
        transport=httpx.MockTransport(transport),
    )
    try:
        with pytest.raises(JudgeUnavailable) as raised:
            judge.probe()
        assert raised.value.code == expected
        assert _SECRET not in str(raised.value)
        assert "function_not_found_for_account" not in str(raised.value)
        assert len(requests) == 1
    finally:
        judge.close()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectTimeout, "timeout"),
        (httpx.ConnectError, "connection_failed"),
        (httpx.RemoteProtocolError, "connection_failed"),
        (httpx.UnsupportedProtocol, "invalid_request"),
        (httpx.InvalidURL, "invalid_request"),
    ],
)
def test_probe_transport_failures_omit_exception_text(error, expected):
    def transport(request):
        raise error(_SECRET)

    judge = LLMJudge(
        "https://judge.test/v1",
        _SECRET,
        "test-model",
        max_retries=0,
        transport=httpx.MockTransport(transport),
    )
    try:
        with pytest.raises(JudgeUnavailable) as raised:
            judge.probe()
        assert raised.value.code == expected
        assert _SECRET not in str(raised.value)
    finally:
        judge.close()


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("https://api.siliconflow.cn/v1", "model_not_found"),
        ("https://api.siliconflow.com/v1/", "model_not_found"),
        ("https://custom.test/v1", "invalid_request"),
    ],
)
def test_siliconflow_numeric_model_code_is_scoped_to_official_endpoints(endpoint, expected):
    judge = LLMJudge(
        endpoint,
        _SECRET,
        "test-model",
        max_retries=0,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(400, json={"code": 20012, "message": _SECRET})
        ),
    )
    try:
        with pytest.raises(JudgeUnavailable) as raised:
            judge.probe()
        assert raised.value.code == expected
        assert _SECRET not in str(raised.value)
    finally:
        judge.close()


@pytest.mark.parametrize("field", ["base_url", "model", "api_key"])
def test_incomplete_probe_does_not_send_request(field):
    def transport(request):
        pytest.fail("An incomplete connection must not make an HTTP request")

    arguments = {"base_url": "https://judge.test/v1", "api_key": _SECRET, "model": "model"}
    arguments[field] = ""
    judge = LLMJudge(**arguments, transport=httpx.MockTransport(transport))
    try:
        with pytest.raises(JudgeUnavailable) as raised:
            judge.probe()
        assert raised.value.code == "invalid_request"
    finally:
        judge.close()


def test_probe_accepts_truncated_chat_completion_without_requiring_judge_json():
    judge = LLMJudge(
        "https://judge.test/v1",
        _SECRET,
        "test-model",
        max_retries=0,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
            )
        ),
    )
    try:
        assert judge.probe() is None
    finally:
        judge.close()


@pytest.mark.parametrize(
    "content",
    [
        b"[" * 1500 + b"0" + b"]" * 1500,
        b"\xff",
        b'{"error":{"code":"insufficient_quota"},"padding":"' + b"x" * (64 * 1024) + b'"}',
    ],
    ids=["nested-json", "invalid-encoding", "oversized-error-body"],
)
def test_probe_bad_error_body_falls_back_to_status_without_raising(content):
    judge = LLMJudge(
        "https://judge.test/v1",
        _SECRET,
        "test-model",
        max_retries=0,
        transport=httpx.MockTransport(lambda request: httpx.Response(400, content=content)),
    )
    try:
        with pytest.raises(JudgeUnavailable) as raised:
            judge.probe()
        assert raised.value.code == "invalid_request"
    finally:
        judge.close()

"""Actual transport attempts, durable-before-post ordering and safe usage observations."""

import httpx
import pytest

from agentlens.infra.llm_judge import LLMJudge


class Hooks:
    def __init__(self, limit=3, fail_finish=False):
        self.events = []
        self.limit = limit
        self.fail_finish = fail_finish
        self.claims = 0

    def claim(self):
        from agentlens.infra.llm_judge.attempts import AttemptRefused

        if self.claims >= self.limit:
            raise AttemptRefused(self.claims)
        self.claims += 1
        self.events.append("committed")
        return str(self.claims)

    def dispatch(self, ticket):
        self.events.append("dispatch")

    def finish(self, ticket, observation):
        self.events.append(observation)
        if self.fail_finish:
            raise RuntimeError("private body secret")


def response(*, usage=None, valid=True, status=200):
    body = {
        "choices": [
            {
                "message": {
                    "content": '{"metrics":[{"name":"q","verdict":"good","reason":"ok"}]}'
                    if valid
                    else "bad json"
                }
            }
        ]
    }
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(status, json=body)


def test_post_is_claimed_first_and_missing_usage_is_unknown():
    hooks = Hooks()

    def post(request):
        assert hooks.events == ["committed", "dispatch"]
        hooks.events.append("post")
        return response()

    with_judge = LLMJudge(
        "https://example.test/v1", "dummy", "m", transport=httpx.MockTransport(post)
    )
    try:
        assert with_judge.judge("fixture", attempts=hooks)["metrics"][0]["name"] == "q"
        obs = hooks.events[-1]
        assert obs.outcome == "response"
        assert obs.usage.total_tokens is None and obs.usage.known_fields == ()
    finally:
        with_judge.close()


@pytest.mark.parametrize("status", [200, 403, 429, 500])
def test_invalid_response_retry_consumes_slots_and_records_partial_zero_usage(status):
    hooks = Hooks(limit=2)
    calls = []

    def post(request):
        calls.append(request)
        return response(usage={"prompt_tokens": 0}, valid=False, status=status)

    judge = LLMJudge(
        "https://example.test/v1", "dummy", "m", backoff_sec=0, transport=httpx.MockTransport(post)
    )
    try:
        with pytest.raises(RuntimeError):
            judge.judge("fixture", attempts=hooks)
        assert len(calls) == (1 if status == 403 else 2)
        observations = [e for e in hooks.events if not isinstance(e, str)]
        assert len(observations) == len(calls)
        assert observations[0].usage.input_tokens == 0
        assert observations[0].usage.output_tokens is None
        assert observations[0].usage.known_fields == ("input_tokens",)
    finally:
        judge.close()


def test_failed_audit_never_retries_http_or_leaks_error():
    hooks = Hooks(fail_finish=True)
    calls = []

    def post(request):
        calls.append(request)
        return response()

    judge = LLMJudge(
        "https://example.test/v1", "dummy", "m", backoff_sec=0, transport=httpx.MockTransport(post)
    )
    try:
        with pytest.raises(RuntimeError) as exc:
            judge.judge("fixture", attempts=hooks)
        assert "secret" not in str(exc.value)
        assert len(calls) == 1
    finally:
        judge.close()


def test_timeout_counts_unknown_usage_without_logging_url_or_body(caplog):
    hooks = Hooks()

    def post(request):
        raise httpx.ReadTimeout("sensitive https://private.test/token", request=request)

    judge = LLMJudge(
        "https://example.test/v1", "dummy", "m", backoff_sec=0, transport=httpx.MockTransport(post)
    )
    try:
        with pytest.raises(RuntimeError) as exc:
            judge.judge("private prompt", attempts=hooks)
        assert "private" not in str(exc.value)
        assert hooks.claims == 3
        assert all(e.usage.known_fields == () for e in hooks.events if not isinstance(e, str))
    finally:
        judge.close()


@pytest.mark.parametrize("body", [None, [], {"choices": {}}, {"choices": [None]}, 42])
def test_bad_envelope_is_accounted_before_retry_and_does_not_escape_parser(body):
    hooks = Hooks()
    calls = []

    def post(request):
        calls.append(request)
        return httpx.Response(
            200,
            content="null" if body is None else __import__("json").dumps(body),
            headers={"content-type": "application/json"},
        )

    judge = LLMJudge(
        "https://example.test/v1", "dummy", "m", backoff_sec=0, transport=httpx.MockTransport(post)
    )
    try:
        with pytest.raises(RuntimeError):
            judge.judge("fixture", attempts=hooks)
        assert len(calls) == 3 and len([e for e in hooks.events if not isinstance(e, str)]) == 3
    finally:
        judge.close()


@pytest.mark.parametrize("verdict", [[], {}])
def test_malformed_metric_verdict_is_received_and_accounted_before_retry(verdict):
    import json

    hooks = Hooks()
    calls = []

    def post(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "usage": {"total_tokens": 4},
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"metrics": [{"name": "q", "verdict": verdict, "reason": "bad"}]}
                            )
                        }
                    }
                ],
            },
        )

    judge = LLMJudge(
        "https://example.test/v1", "dummy", "m", backoff_sec=0, transport=httpx.MockTransport(post)
    )
    try:
        with pytest.raises(RuntimeError):
            judge.judge("fixture", attempts=hooks)
        observations = [e for e in hooks.events if not isinstance(e, str)]
        assert len(calls) == len(observations) == 3
        assert all(e.outcome == "response" and e.usage.total_tokens == 4 for e in observations)
    finally:
        judge.close()

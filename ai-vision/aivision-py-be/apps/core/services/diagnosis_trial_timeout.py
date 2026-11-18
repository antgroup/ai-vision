"""Finite forwarding budgets for synchronous LLM Trials only."""

import math

_DEFAULT_REQUEST_TIMEOUT_SEC = 60
_MAX_REQUEST_TIMEOUT_SEC = 300
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SEC = 3
_FORWARD_OVERHEAD_SEC = 30

LLM_TRIAL_MAX_FORWARD_TIMEOUT = (
    _MAX_ATTEMPTS * _MAX_REQUEST_TIMEOUT_SEC
    + _RETRY_BACKOFF_SEC
    + _FORWARD_OVERHEAD_SEC
)
LLM_TRIAL_TIMEOUT_MESSAGE = "诊断试运行等待超时。后台请求可能仍在执行，请稍后重试。"


def llm_trial_forward_timeout(spec: dict) -> int | float:
    """Cover three Judge attempts and backoff without trusting arbitrary input."""
    if type(spec) is not dict:
        raise ValueError("spec 必须为对象")
    timeout = spec.get("request_timeout_sec", _DEFAULT_REQUEST_TIMEOUT_SEC)
    if (
        type(timeout) not in (int, float)
        or not 0 < timeout <= _MAX_REQUEST_TIMEOUT_SEC
        or not math.isfinite(timeout)
    ):
        raise ValueError(
            "request_timeout_sec 必须是大于 0 且不超过 300 的有限非布尔数值"
        )
    return _MAX_ATTEMPTS * timeout + _RETRY_BACKOFF_SEC + _FORWARD_OVERHEAD_SEC

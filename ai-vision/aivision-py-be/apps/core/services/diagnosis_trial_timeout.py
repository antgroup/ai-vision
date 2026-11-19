"""Finite forwarding budgets for synchronous LLM Trials only."""

import math
from enum import Enum, auto

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


class LlmTrialTimeoutInputError(Enum):
    INVALID_SPEC = auto()
    INVALID_REQUEST_TIMEOUT = auto()


def llm_trial_forward_timeout(spec: object) -> int | float | LlmTrialTimeoutInputError:
    """Cover three Judge attempts and backoff without trusting arbitrary input."""
    if type(spec) is not dict:
        return LlmTrialTimeoutInputError.INVALID_SPEC
    timeout = spec.get("request_timeout_sec", _DEFAULT_REQUEST_TIMEOUT_SEC)
    if (
        type(timeout) not in (int, float)
        or not 0 < timeout <= _MAX_REQUEST_TIMEOUT_SEC
        or not math.isfinite(timeout)
    ):
        return LlmTrialTimeoutInputError.INVALID_REQUEST_TIMEOUT
    return _MAX_ATTEMPTS * timeout + _RETRY_BACKOFF_SEC + _FORWARD_OVERHEAD_SEC

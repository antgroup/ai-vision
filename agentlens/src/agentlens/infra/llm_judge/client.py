"""通用 LLM-as-Judge client（LLM 诊断器的评判基建）。

judge(prompt) → {metrics: [{name, verdict, reason, evidence?}]}：
- 把已渲染的诊断 prompt 与输出格式约束拼成消息，调用 OpenAI 兼容
  /chat/completions，要求模型返回统一的多指标 JSON。
- 重试/超时/限流（429/5xx）→ JudgeUnavailable（调用方按条降级，不崩整批）。

不感知具体运行时或诊断器业务；纯通信原语。配置 base_url/api_key/model 由 service 层注入。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from agentlens.domain.enums import ModelConnectionTestError
from agentlens.domain.llm_options import llm_generation_options
from agentlens.infra.llm_judge.attempts import (
    AttemptAuditError,
    AttemptHooks,
    AttemptObservation,
    AttemptRefused,
    TokenUsage,
)
from agentlens.logging import get_logger


class JudgeUnavailable(RuntimeError):
    """judge 上游不可用（限流/超时/连错/响应不可解析后耗尽重试）。调用方降级，不崩整批。"""

    def __init__(
        self,
        message: str,
        *,
        code: ModelConnectionTestError = ModelConnectionTestError.UNAVAILABLE,
    ) -> None:
        super().__init__(message)
        self.code = code


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_SAFE_ERROR_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")

# Only exact machine codes are interpreted. Messages and arbitrary provider labels
# are never copied into probe exceptions, logs, API responses, or stored state.
_PROBE_ERROR_CODES = {
    "invalid_api_key": ModelConnectionTestError.AUTHENTICATION_FAILED,
    "authentication_error": ModelConnectionTestError.AUTHENTICATION_FAILED,
    "permission_denied": ModelConnectionTestError.PERMISSION_DENIED,
    "permission_error": ModelConnectionTestError.PERMISSION_DENIED,
    "model_not_found": ModelConnectionTestError.MODEL_NOT_FOUND,
    "function_not_found_for_account": ModelConnectionTestError.MODEL_NOT_FOUND,
    "insufficient_quota": ModelConnectionTestError.QUOTA_EXCEEDED,
    "rate_limit_exceeded": ModelConnectionTestError.RATE_LIMITED,
    "rate_limit_error": ModelConnectionTestError.RATE_LIMITED,
    "invalid_request_error": ModelConnectionTestError.INVALID_REQUEST,
}


def _probe_response_error(
    response: httpx.Response, endpoint: str, *, body_limit_bytes: int = 64 * 1024
) -> ModelConnectionTestError | None:
    """Classify explicit failures while preserving the lightweight success probe."""
    status = response.status_code
    # HTTP authentication, missing-resource and server failures take precedence:
    # a 404 never establishes that a credential is invalid.
    by_status = {
        401: ModelConnectionTestError.AUTHENTICATION_FAILED,
        402: ModelConnectionTestError.QUOTA_EXCEEDED,
        404: ModelConnectionTestError.MODEL_NOT_FOUND,
        408: ModelConnectionTestError.TIMEOUT,
        504: ModelConnectionTestError.TIMEOUT,
    }
    if status in by_status:
        return by_status[status]
    if status >= 500 or 300 <= status < 400:
        return ModelConnectionTestError.UNAVAILABLE

    body: dict[str, Any] = {}
    # Parsing is only for finite code lookup; never retain the upstream body.
    if len(response.content) <= body_limit_bytes:
        try:
            parsed = response.json()
        except (ValueError, UnicodeError, RecursionError):
            pass
        else:
            if isinstance(parsed, dict):
                body = parsed
    error = body.get("error")
    error_fields = error if isinstance(error, dict) else body
    codes = (error_fields.get("code"), error_fields.get("type"), body.get("error_code"))
    for code in codes:
        if isinstance(code, str) and code in _PROBE_ERROR_CODES:
            return _PROBE_ERROR_CODES[code]
    # SiliconFlow documents this numeric code; it has no assumed meaning on a
    # custom endpoint or another provider.
    if endpoint.rstrip("/") in {
        "https://api.siliconflow.cn/v1",
        "https://api.siliconflow.com/v1",
    } and error_fields.get("code") in (20012, "20012"):
        return ModelConnectionTestError.MODEL_NOT_FOUND
    if status == 403:
        return ModelConnectionTestError.PERMISSION_DENIED
    if status == 429:
        return ModelConnectionTestError.RATE_LIMITED
    if 400 <= status < 500:
        return ModelConnectionTestError.INVALID_REQUEST
    if error or body.get("error_code") not in (None, 0, "0", ""):
        return ModelConnectionTestError.UNAVAILABLE
    if 200 <= status < 300:
        return None
    return ModelConnectionTestError.UNAVAILABLE


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出提取包含 metrics 的完整 JSON 对象。

    不用正则截取 ``{...}``，否则带 evidence 等嵌套对象的合法 JSON 会被截断。
    """
    cleaned = _THINK_RE.sub("", text).strip()
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except (json.JSONDecodeError, ValueError, UnicodeError, RecursionError):
            continue
        if isinstance(value, dict):
            objects.append(value)
    for value in objects:
        if isinstance(value.get("metrics"), list):
            return value
    return None


_SYSTEM_PROMPT = "你是严谨的 Agent 对话质量评判员。请在内部完成评判，不要输出分析过程。"


def _content_as_text(content: Any) -> str | None:
    """Normalize common OpenAI-compatible message content forms to text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            parts.append(text)
        elif isinstance(text, dict) and isinstance(text.get("value"), str):
            parts.append(text["value"])
    return "\n".join(parts) if parts else None


def _unparseable_detail(resp: httpx.Response) -> str:
    """Return response-shape diagnostics without exposing model output or trace content."""
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError, UnicodeError, RecursionError):
        return "response body is not JSON"
    if not isinstance(body, dict):
        return f"response body type={type(body).__name__}"
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return "response missing choices[0]"
    choice = choices[0]
    if not isinstance(choice, dict):
        return "choices[0] is not an object"
    message = choice.get("message")
    if not isinstance(message, dict):
        return "choices[0] missing message object"
    content = message.get("content")
    if isinstance(content, str):
        return f"message.content string[{len(content)}] lacks a valid judge JSON"
    if isinstance(content, list):
        return f"message.content parts[{len(content)}] lack text with a valid judge JSON"
    return f"message.content type={type(content).__name__}"


def _safe_error_label(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        return None
    label = value.strip()
    allowed = {
        "invalid_request_error",
        "context_length_exceeded",
        "invalid_parameter",
        "rate_limit_exceeded",
        "rate_limit_error",
        "model_not_found",
        "insufficient_quota",
        "content_filter",
        "server_error",
        "authentication_error",
        "permission_denied",
    }
    return label if label in allowed and _SAFE_ERROR_LABEL_RE.fullmatch(label) else None


def _safe_upstream_error_metadata(resp: httpx.Response) -> dict[str, str]:
    """Extract bounded identifiers only; never expose error messages or response bodies."""
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError, UnicodeError, RecursionError):
        return {}
    if not isinstance(body, dict):
        return {}
    nested = body.get("error")
    sources = [nested, body] if isinstance(nested, dict) else [body]
    result: dict[str, str] = {}
    for field in ("type", "code"):
        for source in sources:
            label = _safe_error_label(source.get(field))
            if label is not None:
                result[f"upstream_error_{field}"] = label
                break
    return result


def _safe_error_suffix(metadata: dict[str, str]) -> str:
    values = [
        f"{field}={metadata[field]}"
        for field in ("upstream_error_type", "upstream_error_code")
        if field in metadata
    ]
    return f" ({', '.join(values)})" if values else ""


def _build_messages(
    prompt: str,
    image_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    """诊断 prompt（已渲染变量）+ 输出格式约束 → chat messages。

    prompt 通过 {{var}} 内联所有评判内容。
    JSON 输出协议属于运行时合同，必须放在 system message，避免与用户自定义诊断
    prompt 中的示例同级冲突。业务判定规则和 reason 证据要求由具体诊断器
    prompt 提供。
    """
    fmt = (
        "最终必须输出一个 JSON 对象。每个 metric 必须包含 name、verdict 和非空 reason，例如：\n"
        '{"metrics": [{"name": "任务中声明的 metric key", "verdict": "good", '
        '"reason": "具体判定理由"}]}\n'
        "verdict 只能是 good 或 bad；无论 verdict 为 good 还是 bad，都必须提供非空 reason。\n"
        "reason 必须说明支持当前 verdict 的具体判定依据。\n"
        "evidence 可省略，提供时必须是 JSON object。\n"
        "metrics 必须恰好包含任务中声明的全部 metric key，每个 key 只能出现一次。"
    )
    system_content = (
        f"{_SYSTEM_PROMPT}\n\n"
        "## 输出协议（必须严格遵守）\n"
        f"{fmt}\n"
        "不得输出 Markdown、代码块、分析过程、JSON 外文本或其他字段。"
    )
    user_text = f"## 评判任务\n{prompt}"
    user_content: str | list[dict[str, Any]]
    if image_urls:
        user_content = [{"type": "text", "text": user_text}]
        user_content.extend(
            {"type": "image_url", "image_url": {"url": image_url}} for image_url in image_urls
        )
    else:
        user_content = user_text
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


class LLMJudge:
    """OpenAI 兼容 LLM judge。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 60.0,
        max_retries: int = 2,
        backoff_sec: float = 1.0,
        enable_thinking: bool | None = False,
        temperature: float = 0,
        reasoning_effort: str | int | None = None,
        max_tokens: int | None = None,
        transport: httpx.BaseTransport | None = None,
        response_limit_bytes: int | None = None,
    ) -> None:
        normalized_endpoint = base_url.strip().rstrip("/")
        self._anthropic_compatible = normalized_endpoint == "https://api.anthropic.com/v1"
        self.base_url = normalized_endpoint if self._anthropic_compatible else base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.response_limit_bytes = response_limit_bytes
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_sec = backoff_sec
        self.enable_thinking = enable_thinking
        self.temperature = temperature
        self._generation_options = llm_generation_options(
            {
                key: value
                for key, value in {
                    "reasoning_effort": reasoning_effort,
                    "max_tokens": max_tokens,
                }.items()
                if value is not None
            }
        )
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        self._log = get_logger("llm_judge")
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def judge(
        self,
        prompt: str,
        *,
        image_urls: list[str] | None = None,
        attempts: AttemptHooks | None = None,
    ) -> dict[str, Any]:
        """评判一条。耗尽重试仍失败 → JudgeUnavailable。"""
        messages = _build_messages(prompt, image_urls)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        payload.update(self._generation_options)
        if self._anthropic_compatible:
            # Current Claude models reject temperature=0; the compatibility API
            # ignores reasoning_effort and uses its own thinking defaults.
            payload.pop("temperature", None)
            payload.pop("reasoning_effort", None)
        elif self.enable_thinking is not None:
            payload["enable_thinking"] = self.enable_thinking
            payload["chat_template_kwargs"] = {
                "enable_thinking": self.enable_thinking,
            }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url}/chat/completions"

        last_err = ""
        failure_kind = ""
        status_code: int | None = None
        upstream_error: dict[str, str] = {}
        count = 0
        observations = []
        for attempt in range(min(self.max_retries + 1, 3) if attempts else self.max_retries + 1):
            ticket = None
            if attempts is not None:
                try:
                    ticket = attempts.claim()
                    attempts.dispatch(ticket)
                except AttemptRefused:
                    raise
                except Exception:
                    raise AttemptAuditError("request permission persistence failed") from None
            count += 1
            parsed = None
            stop_retry = False
            usage = TokenUsage()
            try:
                resp = self._post(url, payload, headers)
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                last_err = "request error: " + type(exc).__name__
                failure_kind = "request_error"
                status_code = None
                upstream_error = {}
                error_code = (
                    ModelConnectionTestError.TIMEOUT
                    if isinstance(exc, httpx.TimeoutException)
                    else ModelConnectionTestError.INVALID_REQUEST
                    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol))
                    else ModelConnectionTestError.CONNECTION_FAILED
                    if isinstance(exc, httpx.TransportError)
                    else ModelConnectionTestError.UNAVAILABLE
                )
                observation = AttemptObservation(
                    "request_error", failure_kind="request_error", error_code=error_code
                )
            else:
                status_code = resp.status_code
                try:
                    usage = TokenUsage.from_response(resp.json())
                except (ValueError, json.JSONDecodeError, UnicodeError, RecursionError):
                    pass
                upstream_error = _safe_upstream_error_metadata(resp)
                error_code = _probe_response_error(
                    resp, self.base_url, body_limit_bytes=self.response_limit_bytes or 64 * 1024
                )
                output_limit = bool(resp.extensions.get("response_limit_reached"))
                try:
                    output_limit = output_limit or resp.json()["choices"][0].get(
                        "finish_reason"
                    ) in {"length", "max_tokens"}
                except (
                    ValueError,
                    KeyError,
                    IndexError,
                    TypeError,
                    AttributeError,
                    UnicodeError,
                    RecursionError,
                ):
                    pass
                if resp.status_code >= 400:
                    failure_kind = "http_error"
                    last_err = f"http {resp.status_code}{_safe_error_suffix(upstream_error)}"
                    stop_retry = resp.status_code < 500 and resp.status_code != 429
                else:
                    parsed = self._parse_response(resp)
                    failure_kind = "unparseable_response" if parsed is None else ""
                    last_err = (
                        f"unparseable response: {_unparseable_detail(resp)}"
                        if parsed is None
                        else ""
                    )
                observation = AttemptObservation(
                    "response", status_code, failure_kind or None, usage, error_code, output_limit
                )
            observations.append(observation.to_dict())
            if attempts is not None:
                try:
                    attempts.finish(ticket, observation)
                except Exception:
                    # HTTP must never be repeated merely to repair accounting.
                    raise AttemptAuditError("request observation persistence failed") from None
            if parsed is not None:
                if attempts is not None:
                    parsed["judge_requests"] = observations
                return parsed
            if stop_retry:
                break
            if attempt < self.max_retries:
                time.sleep(self.backoff_sec * (attempt + 1))
        log_fields: dict[str, Any] = {
            "err": last_err,
            "model": self.model,
            "attempts": count,
            "failure_kind": failure_kind,
        }
        if status_code is not None:
            log_fields["status_code"] = status_code
        log_fields.update(upstream_error)
        self._log.warning("llm_judge.unavailable", **log_fields)
        attempt_label = "attempt" if count == 1 else "attempts"
        error = JudgeUnavailable(f"judge failed after {count} {attempt_label}: {last_err}")
        error.observations = observations
        raise error

    def _post(self, url: str, payload: dict, headers: dict) -> httpx.Response:
        if self.response_limit_bytes is None:
            return self._client.post(url, json=payload, headers=headers)
        with self._client.stream("POST", url, json=payload, headers=headers) as response:
            chunks = bytearray()
            for chunk in response.iter_bytes():
                if len(chunks) + len(chunk) > self.response_limit_bytes:
                    # Preserve only transport success/failure, never the partial body.
                    return httpx.Response(
                        response.status_code,
                        content=b"{}",
                        request=response.request,
                        extensions={"response_limit_reached": True},
                    )
                chunks.extend(chunk)
            return httpx.Response(
                response.status_code, content=bytes(chunks), request=response.request
            )

    def probe(self) -> None:
        """Check connectivity and model acceptance without requiring judge JSON."""
        if not self.base_url or not self.model or not self.api_key:
            raise JudgeUnavailable(
                "Model connection parameters are incomplete",
                code=ModelConnectionTestError.INVALID_REQUEST,
            )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "temperature": 0,
            "max_tokens": 1,
        }
        if self._anthropic_compatible:
            payload.pop("temperature")
        elif self.enable_thinking is not None:
            payload["enable_thinking"] = self.enable_thinking
            payload["chat_template_kwargs"] = {
                "enable_thinking": self.enable_thinking,
            }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        failure = ModelConnectionTestError.UNAVAILABLE
        count = 0
        for attempt in range(self.max_retries + 1):
            count += 1
            try:
                response = self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
            except httpx.TimeoutException:
                failure = ModelConnectionTestError.TIMEOUT
            except (httpx.InvalidURL, httpx.UnsupportedProtocol):
                failure = ModelConnectionTestError.INVALID_REQUEST
                break
            except httpx.TransportError:
                failure = ModelConnectionTestError.CONNECTION_FAILED
            except httpx.HTTPError:
                failure = ModelConnectionTestError.UNAVAILABLE
            else:
                failure = _probe_response_error(response, self.base_url)
                if failure is None:
                    return
                if response.status_code < 500 and response.status_code != 429:
                    break
            if attempt < self.max_retries:
                time.sleep(self.backoff_sec * (attempt + 1))
        raise JudgeUnavailable(
            f"Model connection failed after {count} attempts: {failure.value}",
            code=failure,
        )

    def _parse_response(self, resp: httpx.Response) -> dict[str, Any] | None:
        try:
            message = resp.json()["choices"][0]["message"]
        except (
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
            ValueError,
            UnicodeError,
            RecursionError,
        ):
            return None
        if not isinstance(message, dict):
            return None
        content = _content_as_text(message.get("content"))
        if content is None:
            return None
        data = _extract_json(content)
        if data is None:
            return None
        raw_metrics = data.get("metrics")
        if not isinstance(raw_metrics, list) or not raw_metrics:
            return None
        metrics: list[dict[str, Any]] = []
        for item in raw_metrics:
            if not isinstance(item, dict):
                return None
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                return None
            verdict = item.get("verdict")
            if not isinstance(verdict, str) or verdict not in {"good", "bad"}:
                return None
            reason = item.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                return None
            evidence = item.get("evidence", {})
            if not isinstance(evidence, dict):
                return None
            metrics.append(
                {
                    "name": name,
                    "verdict": verdict,
                    "reason": reason,
                    "evidence": evidence,
                }
            )
        return {"metrics": metrics}

    def close(self) -> None:
        self._client.close()

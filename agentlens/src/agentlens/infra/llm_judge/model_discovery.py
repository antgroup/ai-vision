"""Bounded model catalogs for the platform's verified provider endpoints."""

import json
import time
import unicodedata
from typing import Any

import httpx

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_MODELS = 2000
DISCOVERY_TIMEOUT = 10.0

# Exact bases, not arbitrary user-controlled origins or paths. Custom connections
# can still be saved and probed through the existing connection workflow.
_CATALOGS = {
    "https://integrate.api.nvidia.com/v1": ("https://integrate.api.nvidia.com/v1/models", {}),
    "https://api.anthropic.com/v1": ("https://api.anthropic.com/v1/models", {"limit": "100"}),
    "https://api.openai.com/v1": ("https://api.openai.com/v1/models", {}),
    "https://generativelanguage.googleapis.com/v1beta/openai": (
        "https://generativelanguage.googleapis.com/v1beta/openai/models",
        {},
    ),
    "https://api.deepseek.com": ("https://api.deepseek.com/models", {}),
    "https://api.groq.com/openai/v1": ("https://api.groq.com/openai/v1/models", {}),
    "https://api.siliconflow.cn/v1": (
        "https://api.siliconflow.cn/v1/models",
        {"sub_type": "chat"},
    ),
    "https://api.mistral.ai/v1": ("https://api.mistral.ai/v1/models", {}),
    "https://openrouter.ai/api/v1": (
        "https://openrouter.ai/api/v1/models",
        {"output_modalities": "text"},
    ),
    "https://dashscope.aliyuncs.com/compatible-mode/v1": (
        "https://dashscope.aliyuncs.com/api/v1/models",
        {"capabilities": "TG", "supports": "inference", "page_size": "100"},
    ),
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1": (
        "https://dashscope-intl.aliyuncs.com/api/v1/models",
        {"capabilities": "TG", "supports": "inference", "page_size": "100"},
    ),
}

_MESSAGES = {
    "invalid_request": "The model discovery request is invalid.",
    "unsupported_endpoint": "Model discovery is not supported for this endpoint.",
    "missing_api_key": "Enter an API key for this endpoint.",
    "authentication_failed": "The provider rejected the API key.",
    "rate_limited": "The provider rate limit was reached.",
    "unavailable": "The model catalog is temporarily unavailable.",
    "invalid_response": "The provider returned an unsupported model catalog.",
}


class ModelDiscoveryError(Exception):
    def __init__(self, code: str):
        self.code = code
        self.status = (
            422
            if code
            in {
                "invalid_request",
                "unsupported_endpoint",
                "missing_api_key",
            }
            else 502
        )
        super().__init__(_MESSAGES[code])


def normalize_endpoint(endpoint: str) -> str:
    return endpoint.strip().rstrip("/")


def _catalog_response(
    client: httpx.Client,
    url: str,
    params: dict[str, str],
    *,
    started: float,
    remaining_bytes: int,
) -> tuple[dict[str, Any], int]:
    remaining_seconds = DISCOVERY_TIMEOUT - (time.monotonic() - started)
    if remaining_seconds <= 0:
        raise ModelDiscoveryError("unavailable")
    with client.stream("GET", url, params=params, timeout=remaining_seconds) as response:
        if response.status_code in {401, 403}:
            raise ModelDiscoveryError("authentication_failed")
        if response.status_code == 429:
            raise ModelDiscoveryError("rate_limited")
        if not response.is_success:
            raise ModelDiscoveryError("unavailable")
        if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
            raise ModelDiscoveryError("invalid_response")
        length = response.headers.get("content-length")
        if length:
            try:
                if int(length) < 0 or int(length) > remaining_bytes:
                    raise ModelDiscoveryError("invalid_response")
            except ValueError:
                raise ModelDiscoveryError("invalid_response") from None
        body = bytearray()
        # No aggregation buffer: every network chunk must advance the elapsed
        # budget check, including providers trickling less than 16 KiB.
        for chunk in response.iter_bytes():
            if time.monotonic() - started >= DISCOVERY_TIMEOUT:
                raise ModelDiscoveryError("unavailable")
            if len(body) + len(chunk) > remaining_bytes:
                raise ModelDiscoveryError("invalid_response")
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise ModelDiscoveryError("invalid_response") from None
        if not isinstance(payload, dict):
            raise ModelDiscoveryError("invalid_response")
        return payload, len(body)


def _model_id(row: Any, api_key: str, *, qwen: bool) -> str | None:
    if not isinstance(row, dict):
        return None
    capabilities = row.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("completion_chat") is False:
        return None
    if qwen and isinstance(capabilities, list) and "TG" not in capabilities:
        return None
    architecture = row.get("architecture")
    if isinstance(architecture, dict):
        modalities = architecture.get("output_modalities")
        if isinstance(modalities, list) and "text" not in modalities:
            return None
    identifier = row.get("model", row.get("model_name")) if qwen else row.get("id")
    if not isinstance(identifier, str):
        return None
    identifier = identifier.strip()
    if (
        not identifier
        or len(identifier) > 256
        or any(unicodedata.category(char).startswith("C") for char in identifier)
        or api_key in identifier
    ):
        return None
    return identifier


def discover_models(
    endpoint: str,
    api_key: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    endpoint = normalize_endpoint(endpoint)
    catalog = _CATALOGS.get(endpoint)
    if catalog is None:
        raise ModelDiscoveryError("unsupported_endpoint")
    if not api_key or not api_key.strip():
        raise ModelDiscoveryError("missing_api_key")
    if len(api_key) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
        raise ModelDiscoveryError("invalid_request")
    url, default_params = catalog
    qwen = "page_size" in default_params
    anthropic = endpoint == "https://api.anthropic.com/v1"
    started = time.monotonic()
    remaining_bytes = MAX_RESPONSE_BYTES
    identifiers: dict[str, None] = {}
    seen_rows = 0
    after_id: str | None = None
    seen_cursors: set[str] = set()
    auth_headers = (
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        if anthropic
        else {"Authorization": f"Bearer {api_key}"}
    )
    try:
        with httpx.Client(
            timeout=DISCOVERY_TIMEOUT,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={
                **auth_headers,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        ) as client:
            for page in range(1, 21 if qwen or anthropic else 2):
                params = {**default_params, **({"page_no": str(page)} if qwen else {})}
                if after_id is not None:
                    params["after_id"] = after_id
                payload, used = _catalog_response(
                    client, url, params, started=started, remaining_bytes=remaining_bytes
                )
                remaining_bytes -= used
                output = payload.get("output") if qwen else payload
                if not isinstance(output, dict) or (qwen and payload.get("success") is not True):
                    raise ModelDiscoveryError("invalid_response")
                rows = output.get("models" if qwen else "data")
                if not isinstance(rows, list):
                    raise ModelDiscoveryError("invalid_response")
                for row in rows[: MAX_MODELS - seen_rows]:
                    identifier = _model_id(row, api_key, qwen=qwen)
                    if identifier:
                        identifiers[identifier] = None
                seen_rows += len(rows)
                if anthropic:
                    has_more = payload.get("has_more")
                    if not isinstance(has_more, bool):
                        raise ModelDiscoveryError("invalid_response")
                    if not has_more or seen_rows >= MAX_MODELS:
                        break
                    cursor = _model_id({"id": payload.get("last_id")}, api_key, qwen=False)
                    if not rows or cursor is None or cursor in seen_cursors:
                        raise ModelDiscoveryError("invalid_response")
                    seen_cursors.add(cursor)
                    after_id = cursor
                    continue
                if not qwen or not rows or seen_rows >= MAX_MODELS:
                    break
                total = output.get("total")
                if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                    raise ModelDiscoveryError("invalid_response")
                if seen_rows >= total:
                    break
    except httpx.HTTPError:
        # Never echo provider response bodies, request URLs or exception text.
        raise ModelDiscoveryError("unavailable") from None
    return list(identifiers)

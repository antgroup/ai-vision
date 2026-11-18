"""Labs MCP observability API views.

MCP server stats (calls, tool latency, error rates) are read from OpenTelemetry
spans by the trace query pipeline -- they do not need dedicated tables. This
module only persists the sanitized MCP server configs that users paste into the
frontend import dialog so the page can show launch commands and tool metadata
across sessions and devices.
"""

import json
import logging
import math
import os
import re
import select
import subprocess
import time
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ..models import McpServerConfig, Space, SpaceMember
from ..services.trace_query_service import (
    TraceQueryServiceError,
    get_default_trace_query_service,
)
from ..trace_query.semantics.normalizers import coerce_datetime
from .common import check_space_access_or_error, error_response, success_response
from .labs_scan import call_status_stats, collect_span_window, tool_span_outcome

logger = logging.getLogger(__name__)


MAX_CONFIGS_PER_REQUEST = 50
TRANSPORT_CHOICES = {"stdio", "http", "sse"}
# Memory-tool semantics declared on an imported config: tool-name lists per
# memory operation class. Absent keys stay unset; unknown keys are rejected
# so typos fail loudly instead of silently never matching.
MEMORY_SEMANTIC_KEYS = ("searchTools", "writeTools", "listTools", "manageTools")
# Known memory products (2026-10-29 declarations, verified against the real
# tool snapshots of each server): these server ids carry memory semantics
# without an imported config, so a fresh deployment recognizes them out of
# the box and registry updates ship with releases. Imported declarations
# override the builtins; undeclared servers keep the result-shape fallback.
BUILTIN_MEMORY_SERVER_DECLARATIONS: dict[str, dict[str, list[str]]] = {
    "powercontext": {
        "searchTools": [
            "search_memory",
            "search_atomic_memory",
            "search_topic_memory",
        ],
        "writeTools": [
            "remember_memory",
            "replace_artifact",
            "capture_working_note",
        ],
        "listTools": ["list_atomic_memories", "list_memory_entries"],
        "manageTools": [
            "merge_atomic_memories",
            "change_atomic_memory_lifecycle",
            "preview_atomic_memory_restoration",
            "restore_atomic_memory",
            "get_atomic_memory_state",
        ],
    },
    "memos": {
        "writeTools": ["memo_create_memo", "memo_update_memo"],
        "listTools": ["memo_list_memos"],
        "manageTools": ["memo_get_memo", "memo_delete_memo"],
    },
    "openviking": {
        "writeTools": ["remember", "write", "edit"],
        "searchTools": ["find", "search"],
        "listTools": ["list", "tree"],
        "manageTools": ["forget"],
    },
}
MAX_RECENT_CALLS = 200
MAX_MCP_SPAN_PAGE_SIZE = 500
MAX_MCP_SCAN_SPANS = 10000
MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_CLIENT_INFO = {"name": "aivision", "version": "1.0"}
TOOLS_LIST_TIMEOUT_SECONDS = 30
MASK = "******"
SECRET_KEY = re.compile(
    r"token|secret|password|passwd|api[-_]?key|auth|credential", re.I
)
SECRET_VALUE = re.compile(
    r"^(sk-|ghp_|gho_|ghs_|github_pat_|xox[abpr]-|glpat-|Bearer\s)", re.I
)


def _mcp_span_info(span: dict) -> tuple[str, str] | None:
    attributes = span.get("attributes") if isinstance(span, dict) else None
    if not isinstance(attributes, dict):
        return None

    tool_type = attributes.get("agentic.tool.type", "")
    tool_type = tool_type.strip().lower() if isinstance(tool_type, str) else ""
    raw_name = attributes.get("agentic.tool.raw_name") or attributes.get(
        "gen_ai.tool.name"
    )
    raw_name = raw_name.strip() if isinstance(raw_name, str) else ""
    normalized_name = attributes.get("agentic.tool.name")
    normalized_name = (
        normalized_name.strip() if isinstance(normalized_name, str) else ""
    )
    if (
        tool_type != "mcp"
        and str(attributes.get("gen_ai.span.kind", "")).upper() != "MCP"
        and not raw_name.startswith("mcp__")
    ):
        return None

    parts = raw_name.split("__", 2)
    if len(parts) == 3 and all(parts):
        return parts[1], parts[2]
    server_id, separator, tool_name = normalized_name.partition(".")
    if separator and server_id and tool_name:
        return server_id, tool_name
    return None


def _empty_server(server_id: str) -> dict:
    return {
        "serverId": server_id,
        "description": "",
        "sourceAgents": [],
        "calls": [],
        "tools": [],
    }


def _span_time(value: object) -> datetime | None:
    """Span timestamp with malformed values (NaN/Infinity/oversized) tolerated."""
    if isinstance(value, bool):
        return None
    try:
        return coerce_datetime(value)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _mcp_call(span: dict, agent_product: str) -> dict | None:
    info = _mcp_span_info(span)
    if info is None:
        return None
    server_id, tool_name = info
    span_status = span.get("status") if isinstance(span.get("status"), dict) else {}
    status_message = str(span_status.get("message") or "")
    outcome = tool_span_outcome(span)

    start = _span_time(span.get("start_time_unix_nano"))
    end = _span_time(span.get("end_time_unix_nano"))
    attributes = span.get("attributes") or {}
    reported_duration = attributes.get("agentic.tool.duration_ms")
    try:
        duration_ms = (
            float(reported_duration)
            if not isinstance(reported_duration, bool)
            else None
        )
    except (TypeError, ValueError, OverflowError):
        duration_ms = None
    if duration_ms is not None and (not math.isfinite(duration_ms) or duration_ms < 0):
        duration_ms = None
    if duration_ms is None and start is not None and end is not None and end >= start:
        duration_ms = (end - start).total_seconds() * 1_000

    return {
        "serverId": server_id,
        "toolName": tool_name,
        "traceId": str(span.get("trace_id") or ""),
        "spanId": str(span.get("span_id") or ""),
        "agentProduct": str(span.get("agent_product") or agent_product or "").strip(),
        "status": outcome,
        "durationMs": round(duration_ms) if duration_ms is not None else None,
        "timestamp": (
            start.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if start is not None
            else None
        ),
        "lastError": status_message if outcome == "error" else "",
    }


def _serialize_config(config: McpServerConfig) -> dict:
    return {
        "serverId": config.server_id,
        "description": config.description,
        "transport": config.transport,
        "command": config.command,
        "args": config.args,
        "url": config.url,
        "envKeys": config.env_keys,
        "headerKeys": config.header_keys,
        "memory": config.memory_semantics or None,
        "raw": config.raw,
        "importedAt": config.imported_at.isoformat(),
    }


def _sanitize_memory_semantics(value: object) -> dict | None | str:
    """Validate an optional `memory` block on an imported config.

    Returns the normalized declaration, None when absent, or "invalid" when a
    provided block does not parse (the caller rejects the whole config).
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        return "invalid"
    normalized: dict[str, list[str]] = {}
    for key, item in value.items():
        if key not in MEMORY_SEMANTIC_KEYS:
            return "invalid"
        if not isinstance(item, list) or not all(
            isinstance(name, str) and name.strip() for name in item
        ):
            return "invalid"
        names = [name.strip() for name in item]
        if len(set(names)) != len(names):
            return "invalid"
        normalized[key] = names
    if not normalized:
        return "invalid"
    return normalized


def _sanitize_incoming(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    server_id = str(value.get("serverId", "")).strip()
    transport = str(value.get("transport", "")).strip().lower()
    if not server_id or transport not in TRANSPORT_CHOICES:
        return None
    memory = _sanitize_memory_semantics(value.get("memory"))
    if memory == "invalid":
        return None
    if not isinstance(value.get("raw"), dict):
        return None
    for field in ("args", "envKeys", "headerKeys"):
        items = value.get(field)
        if items is None and field == "args":
            continue
        if field not in value:
            continue
        if not isinstance(items, list) or not all(
            isinstance(item, str) for item in items
        ):
            return None
    # Normalize so validation and the PUT defaults see the same values: null
    # fields become "" (str(None) would store the literal "None") and transport
    # is lowercased to match the transport dispatch in tools/list.
    normalized = {
        "serverId": server_id,
        "transport": transport,
        "description": str(value.get("description") or ""),
        "command": str(value.get("command") or ""),
        "url": str(value.get("url") or ""),
        "args": value.get("args"),
        "envKeys": value.get("envKeys", []),
        "headerKeys": value.get("headerKeys", []),
        "memory": memory,
        "raw": value["raw"],
    }
    # CharField columns have fixed limits; an oversized paste would raise
    # DataError (500) instead of this clean 400 rejection.
    if any(
        len(normalized[field]) > limit
        for field, limit in (
            ("serverId", 128),
            ("description", 512),
            ("command", 512),
            ("url", 1024),
        )
    ):
        return None
    normalized["url"] = _sanitize_url(normalized["url"])
    if normalized["args"] is not None:
        normalized["args"] = _sanitize_args(normalized["args"])
    normalized["raw"] = _sanitize_raw(normalized["raw"])
    return normalized


def _sanitize_url(value: str) -> str:
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.netloc:
            return value
        netloc = url.netloc.rsplit("@", 1)[-1]
        if url.username is not None:
            netloc = f"{MASK}@{netloc}"
        query = urlencode(
            [
                (key, MASK if SECRET_KEY.search(key) else item)
                for key, item in parse_qsl(url.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((url.scheme, netloc, url.path, query, url.fragment))
    except ValueError:
        return MASK


def _sanitize_args(args: list[str]) -> list[str]:
    result = []
    for index, arg in enumerate(args):
        flag, separator, inline_value = arg.partition("=")
        previous = args[index - 1] if index else ""
        if separator and flag.startswith("-") and SECRET_KEY.search(flag):
            result.append(f"{flag}={MASK}")
        elif separator and flag.startswith("-"):
            result.append(f"{flag}={_sanitize_args([inline_value])[0]}")
        elif (
            previous.startswith("-")
            and "=" not in previous
            and SECRET_KEY.search(previous)
        ):
            result.append(MASK)
        elif SECRET_VALUE.search(arg):
            result.append(MASK)
        elif ":" in arg and SECRET_KEY.search(arg.partition(":")[0]):
            result.append(f"{arg.partition(':')[0]}: {MASK}")
        else:
            result.append(_sanitize_url(arg))
    return result


def _sanitize_raw(value: object, key: str = "") -> object:
    if key.lower() in {"env", "headers"} and isinstance(value, dict):
        return {name: MASK for name in value}
    if SECRET_KEY.search(key):
        return MASK
    if isinstance(value, dict):
        return {name: _sanitize_raw(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        if key == "args" and all(isinstance(item, str) for item in value):
            return _sanitize_args(value)
        return [_sanitize_raw(item) for item in value]
    if isinstance(value, str):
        return MASK if SECRET_VALUE.search(value) else _sanitize_url(value)
    return value


def _jsonrpc_request(request_id: int | None, method: str, params: object = None):
    request = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if request_id is not None:
        request["id"] = request_id
    if params is not None:
        request["params"] = params
    return request


def _masked_value(value: object) -> bool:
    return isinstance(value, str) and value == "******"


def _jsonrpc_error(value: dict) -> Exception | None:
    error = value.get("error")
    if not isinstance(error, dict):
        return None
    message = str(error.get("message", "MCP tools/list failed"))
    code = error.get("code")
    return Exception(f"{message} ({code})" if code is not None else message)


def _jsonrpc_result(value: object, request_id: int) -> dict:
    if not isinstance(value, dict):
        raise Exception("MCP protocol returned an invalid response")
    error = _jsonrpc_error(value)
    if error:
        raise error
    if value.get("id") != request_id:
        raise Exception("MCP protocol returned an unexpected response id")
    result = value.get("result")
    if not isinstance(result, dict):
        raise Exception("MCP protocol returned an invalid result")
    return result


def _sse_messages(response: requests.Response):
    # SSE is always UTF-8, including when Content-Type omits charset. Requests
    # otherwise defaults text/* to Latin-1, corrupting non-ASCII tool metadata.
    response.encoding = "utf-8"
    data_lines: list[str] = []
    event = ""
    for raw_line in response.iter_lines(decode_unicode=True):
        line = str(raw_line or "")
        if line.startswith("event:"):
            event = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            data_lines.append(line.partition(":")[2].lstrip())
        elif not line and data_lines:
            yield event, data_lines
            event = ""
            data_lines = []
    if data_lines:
        yield event, data_lines


def _result_from_response(response: requests.Response, request_id: int) -> dict:
    content_type = response.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        return _matching_result(_sse_json_messages(_sse_messages(response)), request_id)
    try:
        return _jsonrpc_result(response.json(), request_id)
    except ValueError as exc:
        raise Exception("MCP HTTP response is not valid JSON") from exc


def _sse_json_messages(messages):
    for event, data_lines in messages:
        if event and event not in {"message", "jsonrpc"}:
            continue
        try:
            yield json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            continue


def _matching_result(messages, request_id: int) -> dict:
    for value in messages:
        if isinstance(value, dict) and value.get("id") == request_id:
            return _jsonrpc_result(value, request_id)
    raise Exception("MCP stream closed before returning the requested result")


def _initialize_request() -> dict:
    return _jsonrpc_request(
        1,
        "initialize",
        {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": MCP_CLIENT_INFO,
        },
    )


def _headers(config: McpServerConfig, header_values: dict | None = None) -> dict:
    headers = {"Accept": "application/json, text/event-stream"}
    raw = config.raw if isinstance(config.raw, dict) else {}
    raw_headers = raw.get("headers")
    if isinstance(raw_headers, dict):
        headers.update(
            {
                str(key): str(value)
                for key, value in raw_headers.items()
                if not _masked_value(value)
            }
        )
    # Request-scoped auth values from the sync dialog: accepted only for keys
    # the config already declares, used for this fetch and never persisted.
    if isinstance(header_values, dict):
        allowed = {str(key) for key in config.header_keys or []}
        headers.update(
            {
                str(key): str(value)
                for key, value in header_values.items()
                if str(key) in allowed and str(value)
            }
        )
    return headers


def _stdio_process(config: McpServerConfig) -> subprocess.Popen[str]:
    if not config.command:
        raise Exception("stdio MCP config has no command")
    raw = config.raw if isinstance(config.raw, dict) else {}
    raw_env = raw.get("env")
    # An explicitly enabled local MCP process must not inherit DB credentials,
    # signing keys, or service tokens from the Django worker environment.
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SYSTEMROOT"}
    }
    if isinstance(raw_env, dict):
        env.update(
            {
                str(key): str(value)
                for key, value in raw_env.items()
                if not _masked_value(value)
            }
        )
    return subprocess.Popen(
        [config.command, *(config.args or [])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=False,
        env=env,
    )


def _read_stdio_messages(process: subprocess.Popen[str], deadline: float):
    stdout = process.stdout
    if stdout is None:
        raise Exception("stdio MCP process has no output")

    buffered = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Exception("Timed out waiting for MCP tools/list")
        ready, _, _ = select.select([stdout], [], [], remaining)
        if not ready:
            continue
        chunk = os.read(stdout.fileno(), 65536)
        if not chunk:
            raise Exception("stdio MCP process exited before tools/list")
        buffered += chunk
        while b"\n" in buffered:
            line, buffered = buffered.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _stdio_tools(config: McpServerConfig) -> list[dict]:
    process = _stdio_process(config)
    stdin = process.stdin
    if stdin is None:
        raise Exception("stdio MCP process has no input")
    deadline = time.monotonic() + TOOLS_LIST_TIMEOUT_SECONDS
    try:
        messages = _read_stdio_messages(process, deadline)
        stdin.write(f"{json.dumps(_initialize_request())}\n".encode())
        stdin.flush()
        initialize_result = _matching_result(messages, 1)
        if not isinstance(initialize_result, dict):
            raise Exception("MCP protocol returned an invalid initialize result")

        request = _jsonrpc_request(None, "notifications/initialized")
        stdin.write(f"{json.dumps(request)}\n".encode())
        stdin.flush()

        def request_page(request_id, cursor):
            request = _jsonrpc_request(
                request_id, "tools/list", {"cursor": cursor} if cursor else None
            )
            stdin.write(f"{json.dumps(request)}\n".encode())
            stdin.flush()
            return _matching_result(messages, request_id)

        return _collect_tool_pages(request_page)
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        stdin.close()
        if process.stdout:
            process.stdout.close()


def _loopback_gateway_url(url: str, in_container: bool | None = None) -> str:
    """Point loopback urls at the docker gateway when running in a container.

    A pasted config like `http://127.0.0.1:8003/mcp` describes the host where
    the agent client runs. The be container cannot reach its own loopback, so
    the fetch has to go through host.docker.internal instead. Outside a
    container the url is used as-is. Stored configs keep the original url.
    """
    if in_container is None:
        in_container = os.path.exists("/.dockerenv")
    if not in_container:
        return url
    return re.sub(
        r"^(https?://)(127\.0\.0\.1|localhost)(?=[/:]|$)",
        r"\1host.docker.internal",
        url,
        count=1,
    )


def _check_http_response(response):
    # Redirects can disclose custom credentials to an unrelated endpoint.
    if 300 <= response.status_code < 400:
        raise Exception("MCP endpoint redirects are not supported")
    response.raise_for_status()


def _post_notification(url, headers, payload):
    response = requests.post(
        url, json=payload, headers=headers, timeout=(3, 3), allow_redirects=False
    )
    try:
        _check_http_response(response)
    finally:
        response.close()


def _collect_tool_pages(request_page) -> list[dict]:
    tools = []
    cursor = None
    seen = set()
    for request_id in range(2, 22):
        result = request_page(request_id, cursor)
        page_tools = result.get("tools")
        if not isinstance(page_tools, list):
            raise Exception("MCP tools/list returned no tools array")
        tools.extend(tool for tool in page_tools if isinstance(tool, dict))
        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            return tools
        if next_cursor in seen:
            raise Exception("MCP server repeated a tools/list cursor")
        seen.add(next_cursor)
        cursor = next_cursor
    raise Exception("MCP server exceeded the tools/list page limit")


def _http_tools(
    config: McpServerConfig, header_values: dict | None = None
) -> list[dict]:
    if not config.url:
        raise Exception("http MCP config has no url")
    url = _loopback_gateway_url(config.url)
    headers = {**_headers(config, header_values), "Content-Type": "application/json"}
    response = requests.post(
        url,
        json=_initialize_request(),
        headers=headers,
        stream=True,
        timeout=(3, TOOLS_LIST_TIMEOUT_SECONDS),
        allow_redirects=False,
    )
    # Some MCP servers canonicalize /mcp to /mcp/. Permit only that exact
    # method-preserving redirect so credentials cannot leave the endpoint.
    if response.status_code in {307, 308}:
        location = response.headers.get("Location")
        target = urljoin(url, location) if location else ""
        original = urlsplit(url)
        canonical = original._replace(path=original.path + "/").geturl()
        if target == canonical:
            response.close()
            url = target
            response = requests.post(
                url,
                json=_initialize_request(),
                headers=headers,
                stream=True,
                timeout=(3, TOOLS_LIST_TIMEOUT_SECONDS),
                allow_redirects=False,
            )
    try:
        _check_http_response(response)
        initialize_result = _result_from_response(response, 1)
        headers["MCP-Protocol-Version"] = initialize_result.get(
            "protocolVersion", MCP_PROTOCOL_VERSION
        )
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
    finally:
        response.close()

    _post_notification(
        url, headers, _jsonrpc_request(None, "notifications/initialized")
    )

    def request_page(request_id, cursor):
        response = requests.post(
            url,
            json=_jsonrpc_request(
                request_id, "tools/list", {"cursor": cursor} if cursor else None
            ),
            headers=headers,
            stream=True,
            timeout=(3, TOOLS_LIST_TIMEOUT_SECONDS),
            allow_redirects=False,
        )
        try:
            _check_http_response(response)
            return _result_from_response(response, request_id)
        finally:
            response.close()

    return _collect_tool_pages(request_page)


def _url_origin(url: str):
    parsed = urlsplit(url)
    return (
        parsed.scheme,
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def _sse_tools(
    config: McpServerConfig, header_values: dict | None = None
) -> list[dict]:
    if not config.url:
        raise Exception("sse MCP config has no url")
    url = _loopback_gateway_url(config.url)
    headers = {**_headers(config, header_values), "Accept": "text/event-stream"}
    event_response = requests.get(
        url,
        headers=headers,
        stream=True,
        timeout=(3, TOOLS_LIST_TIMEOUT_SECONDS),
        allow_redirects=False,
    )
    try:
        _check_http_response(event_response)
        messages = _sse_messages(event_response)
        endpoint = ""
        for event, data_lines in messages:
            if event == "endpoint" and data_lines:
                endpoint = urljoin(url, data_lines[0])
                break
        if not endpoint:
            raise Exception("SSE MCP server did not send an endpoint event")
        if (
            _url_origin(endpoint) != _url_origin(url)
            or urlsplit(endpoint).username is not None
        ):
            raise Exception("SSE MCP endpoint must use the same origin")

        request_headers = {
            **_headers(config, header_values),
            "Content-Type": "application/json",
        }
        responses = _sse_json_messages(messages)
        _post_notification(endpoint, request_headers, _initialize_request())
        _matching_result(responses, 1)
        _post_notification(
            endpoint,
            request_headers,
            _jsonrpc_request(None, "notifications/initialized"),
        )

        def request_page(request_id, cursor):
            _post_notification(
                endpoint,
                request_headers,
                _jsonrpc_request(
                    request_id, "tools/list", {"cursor": cursor} if cursor else None
                ),
            )
            return _matching_result(responses, request_id)

        return _collect_tool_pages(request_page)
    finally:
        event_response.close()


def list_mcp_tools(
    config: McpServerConfig, header_values: dict | None = None
) -> list[dict]:
    """List tools through the standard MCP JSON-RPC tools/list method."""
    if config.transport == "stdio":
        return _stdio_tools(config)
    if config.transport == "http":
        return _http_tools(config, header_values)
    if config.transport == "sse":
        return _sse_tools(config, header_values)
    raise Exception(f"Unsupported MCP transport: {config.transport}")


class McpConfigListView(APIView):
    """GET list or PUT upsert MCP server configs for a space."""

    permission_classes = [IsAuthenticated]

    def get(self, request, space_id: str):
        try:
            space = Space.objects.get(uuid=space_id, status=1, gmt_deleted__isnull=True)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001, 404)
        denied = check_space_access_or_error(request.user, space)
        if denied:
            return denied
        rows = McpServerConfig.objects.filter(
            space=space, gmt_deleted__isnull=True
        ).order_by("server_id")
        return success_response([_serialize_config(row) for row in rows])

    def put(self, request, space_id: str):
        try:
            space = Space.objects.get(uuid=space_id, status=1, gmt_deleted__isnull=True)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001, 404)
        denied = check_space_access_or_error(request.user, space)
        if denied:
            return denied
        denied = _check_mcp_admin(request.user, space)
        if denied:
            return denied

        configs = (
            request.data.get("configs") if isinstance(request.data, dict) else None
        )
        if not isinstance(configs, list) or not configs:
            return error_response("configs must be a non-empty list", 3003)
        if len(configs) > MAX_CONFIGS_PER_REQUEST:
            return error_response(
                f"At most {MAX_CONFIGS_PER_REQUEST} configs per request", 3004
            )

        now = timezone.now()
        sanitized = []
        for item in configs:
            parsed = _sanitize_incoming(item)
            if parsed is None:
                return error_response("Invalid MCP server config payload", 3005)
            sanitized.append(parsed)

        with transaction.atomic():
            for item in sanitized:
                config, _ = McpServerConfig.objects.select_for_update().get_or_create(
                    space=space,
                    server_id=item["serverId"],
                )
                defaults = {
                    "description": item["description"],
                    "transport": item["transport"],
                    "command": item["command"],
                    "args": item["args"],
                    "url": item["url"],
                    "env_keys": item["envKeys"],
                    "header_keys": item["headerKeys"],
                    "memory_semantics": item["memory"],
                    "raw": item["raw"],
                    "imported_at": now,
                    "gmt_deleted": None,
                }
                connection_fields = (
                    "transport",
                    "command",
                    "args",
                    "url",
                    "env_keys",
                    "header_keys",
                    "raw",
                )
                update_fields = [*defaults, "gmt_modified"]
                if config.gmt_deleted or any(
                    getattr(config, key) != defaults[key] for key in connection_fields
                ):
                    config.tools = []
                    config.tools_synced_at = None
                    update_fields.extend(["tools", "tools_synced_at"])
                for key, value in defaults.items():
                    setattr(config, key, value)
                # An unchanged import must not overwrite a newer sync snapshot
                # with the values read into this model instance.
                config.save(update_fields=update_fields)
        rows = McpServerConfig.objects.filter(
            space=space, gmt_deleted__isnull=True
        ).order_by("server_id")
        return success_response([_serialize_config(row) for row in rows])


class McpConfigDetailView(APIView):
    """DELETE a single MCP server config by serverId (soft delete)."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, space_id: str, server_id: str):
        try:
            space = Space.objects.get(uuid=space_id, status=1, gmt_deleted__isnull=True)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001, 404)
        denied = check_space_access_or_error(request.user, space)
        if denied:
            return denied
        denied = _check_mcp_admin(request.user, space)
        if denied:
            return denied
        now = timezone.now()
        updated = McpServerConfig.objects.filter(
            space=space,
            server_id=server_id,
            gmt_deleted__isnull=True,
        ).update(gmt_deleted=now)
        if not updated:
            return error_response("Config not found", 3006, 404)
        return success_response(None)


def _serialize_config_tools(config: McpServerConfig) -> dict:
    return {
        "tools": config.tools if isinstance(config.tools, list) else [],
        "syncedAt": (
            config.tools_synced_at.isoformat() if config.tools_synced_at else None
        ),
    }


def _check_mcp_admin(user, space):
    if not SpaceMember.objects.filter(
        space=space, user=user, role=1, gmt_deleted__isnull=True
    ).exists():
        return error_response(
            "Only space administrators can manage or sync MCP configs", 3002, 403
        )
    return None


class McpConfigToolsView(APIView):
    """Read or refresh the stored tools/list snapshot for one config.

    Only space administrators may refresh. HTTP/SSE fetch the configured
    address server-side. stdio additionally requires deployment opt-in because
    it runs an unsandboxed process with the backend's OS account.

    GET returns the stored snapshot without any network traffic. POST
    re-fetches through the MCP protocol and persists the result;
    `headerValues` in the body supplies request-scoped auth values (only
    keys already declared in the config's headerKeys) which are never
    stored or logged. Non-empty headerValues require expectedImportedAt to
    match the config version the client used when collecting those values.
    """

    permission_classes = [IsAuthenticated]

    def _find_config(self, request, space_id: str, server_id: str):
        try:
            space = Space.objects.get(uuid=space_id, status=1, gmt_deleted__isnull=True)
        except Space.DoesNotExist:
            return None, error_response("Space not found", 3001, 404)
        denied = check_space_access_or_error(request.user, space)
        if denied:
            return None, denied
        config = McpServerConfig.objects.filter(
            space=space,
            server_id=server_id,
            gmt_deleted__isnull=True,
        ).first()
        if config is None:
            return None, error_response("MCP config not found", 3010, 404)
        return config, None

    def get(self, request, space_id: str, server_id: str):
        config, error = self._find_config(request, space_id, server_id)
        if error:
            return error
        return success_response(_serialize_config_tools(config))

    def post(self, request, space_id: str, server_id: str):
        config, error = self._find_config(request, space_id, server_id)
        if error:
            return error
        denied = _check_mcp_admin(request.user, config.space)
        if denied:
            return denied
        if config.transport == "stdio" and not getattr(
            settings, "MCP_STDIO_SYNC_ENABLED", False
        ):
            return error_response(
                "stdio MCP sync is disabled; enable MCP_STDIO_SYNC_ENABLED only in a trusted deployment",
                3021,
                403,
            )
        body = request.data if isinstance(request.data, dict) else {}
        header_values = body.get("headerValues")
        if header_values is not None and not isinstance(header_values, dict):
            return error_response("headerValues must be an object", 3003)
        if (
            header_values
            and body.get("expectedImportedAt") != config.imported_at.isoformat()
        ):
            return error_response(
                "MCP config version is missing or changed; refresh the config before syncing",
                3022,
                409,
            )
        try:
            tools = list_mcp_tools(config, header_values)
        except Exception as exc:
            # InvalidHeader and remote JSON-RPC errors may contain credentials.
            # Log the failure category, never exception text or a traceback.
            logger.warning("MCP tools/list failed (%s)", type(exc).__name__)
            return error_response(
                "MCP tools/list failed; check server address, credentials and protocol",
                3020,
                502,
            )
        config.tools = tools
        config.tools_synced_at = timezone.now()
        updated = McpServerConfig.objects.filter(
            pk=config.pk,
            imported_at=config.imported_at,
            gmt_deleted__isnull=True,
            space__status=1,
            space__gmt_deleted__isnull=True,
        ).update(
            tools=tools,
            tools_synced_at=config.tools_synced_at,
            gmt_modified=timezone.now(),
        )
        if not updated:
            return error_response(
                "MCP config changed while syncing; retry the sync", 3022, 409
            )
        return success_response(_serialize_config_tools(config))


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _serialize_call_stats(calls: list[dict]) -> dict:
    durations = [call["durationMs"] for call in calls if call["durationMs"] is not None]
    return {
        "calls": len(calls),
        "measuredCalls": len(durations),
        **call_status_stats(call["status"] for call in calls),
        "p50Ms": _percentile(durations, 0.5),
        "p95Ms": _percentile(durations, 0.95),
    }


def _declared_memory_server_ids(space) -> frozenset[str]:
    """Server ids with memory semantics: imported declarations plus the
    known memory products. Same registry test the memory page uses to admit
    a server; excluding those servers here keeps MCP observability and memory
    observability disjoint instead of double-counting the same spans.
    """
    rows = McpServerConfig.objects.filter(
        space=space, gmt_deleted__isnull=True, memory_semantics__isnull=False
    ).only("server_id", "memory_semantics")
    declared = {
        row.server_id
        for row in rows
        if isinstance(row.memory_semantics, dict)
        and any(key in row.memory_semantics for key in MEMORY_SEMANTIC_KEYS)
    }
    declared.update(
        server_id
        for server_id in BUILTIN_MEMORY_SERVER_DECLARATIONS
        if server_id not in declared
    )
    return frozenset(declared)


def _serialize_scanned_observations(
    scans: list[dict],
    available_traces: int = 0,
    scan_count: int | None = None,
    excluded_server_ids: frozenset[str] = frozenset(),
):
    # Match Memory's per-call recognition without swallowing ordinary tools
    # offered by an undeclared server that also has memory calls.
    from .labs_memory import _memory_call

    memory_server_ids = set(excluded_server_ids)
    aggregate: dict[str, dict] = {}
    tools_by_server: dict[str, dict[str, dict]] = {}
    for scan in scans:
        agent_product = str(scan.get("agent_product") or "")
        for span in scan.get("spans", []):
            call = _mcp_call(span, agent_product)
            if call is None:
                continue
            if call["serverId"] in excluded_server_ids:
                continue
            memory_call = _memory_call(span, agent_product)
            if memory_call and memory_call["isMemory"]:
                memory_server_ids.add(call["serverId"])
                continue

            server_id = call["serverId"]
            if server_id not in aggregate:
                aggregate[server_id] = _empty_server(server_id)
                tools_by_server[server_id] = {}
            server = aggregate[server_id]
            server["calls"].append(call)
            if (
                call["agentProduct"]
                and call["agentProduct"] not in server["sourceAgents"]
            ):
                server["sourceAgents"].append(call["agentProduct"])

            tool = tools_by_server[server_id].get(call["toolName"])
            if tool is None:
                tool = {
                    "toolName": call["toolName"],
                    "rawName": f"mcp__{call['serverId']}__{call['toolName']}",
                    "calls": 0,
                    "statuses": [],
                    "durations": [],
                    "lastError": "",
                    "lastErrorCall": None,
                }
                server["tools"].append(tool)
                tools_by_server[server_id][call["toolName"]] = tool
            tool["calls"] += 1
            tool["statuses"].append(call["status"])
            if call["durationMs"] is not None:
                tool["durations"].append(call["durationMs"])
            if call["status"] == "error":
                previous = tool["lastErrorCall"]
                if previous is None or _mcp_call_sort_key(call) > _mcp_call_sort_key(
                    previous
                ):
                    tool["lastError"] = call["lastError"]
                    tool["lastErrorCall"] = call

    all_calls = [call for server in aggregate.values() for call in server["calls"]]
    for server in aggregate.values():
        server["stats"] = _serialize_call_stats(server["calls"])
        server["calls"].sort(key=_mcp_call_sort_key, reverse=True)
        server["recentCalls"] = server["calls"][:MAX_RECENT_CALLS]
        del server["calls"]
        for tool in server["tools"]:
            tool["stats"] = {
                "calls": tool["calls"],
                "measuredCalls": len(tool["durations"]),
                **call_status_stats(tool.pop("statuses")),
                "p50Ms": _percentile(tool["durations"], 0.5),
                "p95Ms": _percentile(tool["durations"], 0.95),
                "lastError": tool.pop("lastError"),
                "lastErrorCall": tool.pop("lastErrorCall"),
            }
            tool.pop("calls")
            tool.pop("durations")
        server["tools"].sort(key=lambda item: -item["stats"]["calls"])

    servers = sorted(aggregate.values(), key=lambda item: item["serverId"])
    tool_count = sum(len(server["tools"]) for server in servers)
    all_stats = _serialize_call_stats(all_calls)
    return {
        "overview": {
            "serverCount": len(servers),
            "calledToolCount": tool_count,
            "calls": all_stats["calls"],
            "measuredCalls": all_stats["measuredCalls"],
            "errorRate": all_stats["errorRate"],
            "errors": all_stats["errors"],
            "reportedStatusCalls": all_stats["reportedStatusCalls"],
            "unknownStatusCalls": all_stats["unknownStatusCalls"],
            # "Slowest P95/P50" are the max per-server percentiles, not
            # the pooled ones: a high-volume fast server would otherwise
            # mask a slow one with few calls.
            "p95Ms": max(
                (
                    server["stats"]["p95Ms"]
                    for server in servers
                    if server["stats"]["p95Ms"] is not None
                ),
                default=None,
            ),
            "p50Ms": max(
                (
                    server["stats"]["p50Ms"]
                    for server in servers
                    if server["stats"]["p50Ms"] is not None
                ),
                default=None,
            ),
            "scanned": len(scans) if scan_count is None else scan_count,
            "availableTraces": available_traces,
            "truncated": available_traces
            > (len(scans) if scan_count is None else scan_count),
        },
        "servers": servers,
        "memoryServerIds": sorted(memory_server_ids),
    }


def _mcp_call_sort_key(call: dict):
    timestamp = coerce_datetime(call.get("timestamp"))
    return (
        timestamp is not None,
        timestamp or datetime.min.replace(tzinfo=UTC),
        call.get("traceId", ""),
        call.get("spanId", ""),
    )


def _collect_mcp_spans(
    trace_service, *, space, user, query_params: dict, source_system=None
):
    """Read the complete bounded window, refusing unstable/partial provider pages."""
    def fetch_page(**kwargs):
        if source_system:
            kwargs["source_system_override"] = source_system
        return trace_service.list_mcp_spans(**kwargs)

    return collect_span_window(
        fetch_page,
        space=space,
        user=user,
        query_params=query_params,
        label="MCP",
        page_size=MAX_MCP_SPAN_PAGE_SIZE,
        max_spans=MAX_MCP_SCAN_SPANS,
        limit_code=3027,
        allow_legacy_single_page=True,
        max_workers=1,
    )["spans"]


def _mcp_spans_observability(
    trace_service,
    space,
    user,
    agent_product: str,
    from_ts: str,
    to_ts: str,
    *,
    history=None,
    agent_uuid=None,
    source_system=None,
    excluded_server_ids: frozenset[str] | None = None,
):
    query_params = {"from": from_ts, "to": to_ts, "limit": MAX_MCP_SPAN_PAGE_SIZE}
    if agent_product:
        query_params["agent_product"] = agent_product
    if agent_uuid:
        query_params["agent_uuid"] = agent_uuid
    spans = _collect_mcp_spans(
        trace_service,
        space=space,
        user=user,
        query_params=query_params,
        source_system=source_system,
    )
    if excluded_server_ids is None:
        excluded_server_ids = _declared_memory_server_ids(space)
    if history is not None:
        from .labs_memory import _memory_call

        calls = []
        for span in spans:
            call = _mcp_call(span, agent_product)
            if (
                call is None
                or call["serverId"] != history["serverId"]
                or call["serverId"] in excluded_server_ids
            ):
                continue
            memory_call = _memory_call(span, agent_product)
            if memory_call and memory_call["isMemory"]:
                continue
            calls.append(call)
        total_calls = len(calls)
        if history["status"] != "all":
            calls = [call for call in calls if call["status"] == history["status"]]
        if history["sort"] == "duration":
            calls.sort(
                key=lambda call: (
                    call["durationMs"] is not None,
                    call["durationMs"] or 0,
                    _mcp_call_sort_key(call),
                ),
                reverse=True,
            )
        else:
            calls.sort(key=_mcp_call_sort_key, reverse=history["sort"] != "oldest")
            if history["sort"] == "oldest":
                calls.sort(key=lambda call: call["timestamp"] is None)
        total = len(calls)
        page_size = history["pageSize"]
        page = min(history["page"], max(1, math.ceil(total / page_size)))
        return {
            "calls": calls[(page - 1) * page_size : page * page_size],
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalCalls": total_calls,
            "complete": True,
            "scan": {
                "scanned": len(spans),
                "availableTraces": len(spans),
                "truncated": False,
            },
        }
    result = _serialize_scanned_observations(
        [{"agent_product": agent_product, "spans": spans}],
        available_traces=len(spans),
        scan_count=len(spans),
        excluded_server_ids=excluded_server_ids,
    )

    result["scope"] = {"from": from_ts, "to": to_ts}
    return result


class McpObservabilityView(APIView):
    """Aggregate MCP tool calls from traces in the selected time window."""

    permission_classes = [IsAuthenticated]

    def get(self, request, space_id: str):
        try:
            # status=1 keeps disabled spaces out; gmt_deleted matches the
            # soft-delete filter the config views in this module use.
            space = Space.objects.get(uuid=space_id, status=1, gmt_deleted__isnull=True)
        except Space.DoesNotExist:
            return error_response("Space not found", 3001, 404)
        denied = check_space_access_or_error(request.user, space)
        if denied:
            return denied

        from_ts = request.query_params.get("fromTimestamp", "")
        to_ts = request.query_params.get("toTimestamp", "")
        if not from_ts or not to_ts:
            return error_response("fromTimestamp and toTimestamp are required", 3003)
        agent_product = str(request.query_params.get("agent_product", "")).strip()

        history = None
        if request.query_params.get("historyServer"):
            try:
                page = int(request.query_params.get("page", "1"))
                page_size = int(request.query_params.get("pageSize", "5"))
            except (ValueError, TypeError):
                return error_response("Invalid MCP history page", 3003)
            status = request.query_params.get("status", "all")
            sort = request.query_params.get("sort", "newest")
            if (
                page < 1
                or not 1 <= page_size <= 100
                or status not in {"all", "ok", "error", "unknown"}
                or sort not in {"newest", "oldest", "duration"}
            ):
                return error_response("Invalid MCP history filters", 3003)
            history = {
                "serverId": request.query_params["historyServer"],
                "page": page,
                "pageSize": page_size,
                "status": status,
                "sort": sort,
            }

        try:
            traces = _mcp_spans_observability(
                get_default_trace_query_service(),
                space,
                request.user,
                agent_product,
                from_ts,
                to_ts,
                history=history,
                agent_uuid=request.query_params.get("agent_uuid"),
                source_system=request.query_params.get("source_system"),
            )
        except TraceQueryServiceError as exc:
            logger.warning("MCP trace scan failed", exc_info=True)
            return error_response(exc.message, exc.code)
        except requests.RequestException:
            logger.warning("MCP trace scan request failed", exc_info=True)
            return error_response("Failed to scan MCP traces", 3012)

        return success_response(traces)

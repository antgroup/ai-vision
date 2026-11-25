"""Labs Memory observability API views.

Memory-server statistics are aggregated from MCP and native operation spans in the selected
window — memory observability has no separate data source. Which MCP servers
carry memory semantics comes from the imported config registry
(`McpServerConfig.memory_semantics`, declared per server by the importer);
servers without a declaration are still recognized from their verified span
result shapes. Everything else (calls, latency, error rates) mirrors the MCP
observability pipeline.
"""

import json
import logging
import math
import re
from datetime import UTC, datetime

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from ..models import McpServerConfig, Space
from ..services.trace_query_service import (
    TraceQueryServiceError,
    get_default_trace_query_service,
)
from .common import check_space_access_or_error, error_response, success_response
from .labs_mcp import (
    BUILTIN_MEMORY_SERVER_DECLARATIONS,
    MAX_MCP_SPAN_PAGE_SIZE,
    MAX_RECENT_CALLS,
    MEMORY_SEMANTIC_KEYS,
    _mcp_span_info,
    _span_time,
)
from .labs_scan import (
    call_status_stats,
    collect_span_window,
    latency_stats,
    tool_span_outcome,
)

logger = logging.getLogger(__name__)

# Storage span page ceiling, mirroring the MCP / skill scans.
MAX_MEMORY_SCAN_SPANS = 10_000

# Verified memory result shapes (2026-10-23 survey of PowerContext, extended
# 2026-10-26 with structured hybrid-search JSON and 2026-10-29 with native
# artifact write/list results; the fallback for undeclared servers).
# A shape that does not parse
# degrades to "no evidence" instead of a guessed metric.
MATCHED_RE = re.compile(r"Matched (\d+) memories", re.I)
STORED_RE = re.compile(r"Stored memory\s+(\S+)(?:\s+\(kind=([\w-]+),\s*state=([\w-]+))?")
MEMORY_NOTE_PREFIX = "Working note recorded"
MEMORIES_ARRAY_KEY = "memories"
# usememos `memos` MCP (2026-10-29 trace 9df6fa7f): list results carry the
# memo records under a top-level `memos` array, each with a `content` string
# and a usememos state (NORMAL/PINNED/ARCHIVED); create results are the
# created memo resource itself (`content` + `name` + `visibility`).
MEMOS_ARRAY_KEY = "memos"
# OpenViking write result line (2026-10-29 trace 9df6fa7f):
# "Wrote 118 bytes to viking://user/<user>/memories/<file>.md (mode=replace)."
WROTE_RE = re.compile(r"Wrote \d+ bytes to (viking://\S+/memories/\S+)")
# OpenViking find result (same trace): "Found 7 item(s):" followed by
# "- [memory 87%] viking://…/<file>.md\n    <content>" items. Only the memory
# items count as memory hits; resource/skill-only finds stay out so ordinary
# document lookups never pollute the memory metrics.
FOUND_RE = re.compile(r"Found \d+ item\(s\):")
FOUND_MEMORY_ITEM_RE = re.compile(r"^- \[memory \d+%\] \S+\n\s+(.+)$", re.M)
# Structured hybrid-search shape: the top-level `hits` array carries one hit
# per memory, with the entry record nested under `memory`
# ({"mode":"hybrid","hits":[{"memory":{"text","state"},"score"}]}).
HITS_ARRAY_KEY = "hits"
# `1. [active] <content> (score 0.82)` numbered lines of a search result.
HIT_LINE_RE = re.compile(
    r"^\s*\d+\.\s+\[([^\]]+)\]\s+(.+?)\s*(?:\(score\s+[0-9.]+\))?\s*$", re.M
)


def _scalar_text(value: object) -> str:
    """Mirrors the frontend argument summary bound (40 chars per value)."""
    text = str(value)
    return text if len(text) <= 40 else f"{text[:40]}…"


def _summarize_arguments(args_raw: object) -> str:
    """Compact one-line argument summary, mirroring the span-card renderer."""
    parsed: object = None
    if isinstance(args_raw, str) and args_raw.strip():
        try:
            parsed = json.loads(args_raw)
        except ValueError:
            text = args_raw.strip()
            return text if len(text) <= 80 else f"{text[:80]}…"
    elif isinstance(args_raw, dict):
        parsed = args_raw
    if not isinstance(parsed, dict):
        return ""
    parts = [
        f"{key}={_scalar_text(value)}"
        for key, value in parsed.items()
        if not isinstance(value, (dict, list))
    ]
    if not parts:
        return ""
    summary = " · ".join(parts[:4])
    return summary if len(parts) <= 4 else f"{summary} · …"


def _hybrid_hit_entries(hits: list) -> list[dict]:
    """Extract {state, content} entries from a hybrid-search `hits` array;
    each hit nests its record under `memory` (fall back to the hit itself).
    Hits without usable text stay out instead of padding empty rows."""
    entries: list[dict] = []
    for hit in hits:
        record = hit.get("memory") if isinstance(hit, dict) else None
        if not isinstance(record, dict):
            record = hit if isinstance(hit, dict) else None
        if not isinstance(record, dict):
            continue
        text = record.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        state = record.get("state")
        entries.append(
            {
                "state": state.strip() if isinstance(state, str) else "",
                "content": text.strip(),
            }
        )
    return entries


def _powercontext_record(record: object) -> bool:
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("text"), str)
        or not record["text"].strip()
    ):
        return False
    artifact = record.get("artifact")
    return (
        isinstance(artifact, dict)
        and artifact.get("family") == "atomic-memory"
        and isinstance(artifact.get("artifact_id"), str)
        and bool(artifact["artifact_id"].strip())
    )


def _memory_hit(hit: object) -> bool:
    if not isinstance(hit, dict):
        return False
    record = hit.get("memory")
    if isinstance(record, dict):
        return isinstance(record.get("text"), str) and bool(record["text"].strip())
    return _powercontext_record(hit)


def _memory_evidence(
    result_raw: object, category: str | None = None, depth: int = 0
) -> dict | None:
    """Classify a tool result by the verified memory result shapes.

    Returns {"type": search|write|list|note, ...parsed fields} or None when
    the shape is unknown — callers treat None as "no memory evidence".
    """
    if depth > 4:
        return None
    parsed = result_raw
    result_text = result_raw.strip() if isinstance(result_raw, str) else ""
    wrapped = re.fullmatch(
        r"Wall time: [0-9]+(?:\.[0-9]+)? seconds\r?\nOutput:\r?\n(.*)",
        result_text,
        re.DOTALL,
    )
    if wrapped:
        return _memory_evidence(wrapped.group(1), category, depth + 1)
    if result_text.startswith(("{", "[")):
        try:
            parsed = json.loads(result_text)
        except ValueError:
            parsed = None
    if isinstance(parsed, dict):
        if parsed.get("isError") is True:
            return None
        structured = parsed.get("structuredContent")
        if isinstance(structured, dict):
            evidence = _memory_evidence(structured, category, depth + 1)
            if evidence:
                return evidence
        content = parsed.get("content")
        if isinstance(content, list):
            texts = [
                item["text"]
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ]
            evidence = _memory_evidence("\n".join(texts), category, depth + 1)
            if evidence:
                return evidence
        memories = parsed.get(MEMORIES_ARRAY_KEY)
        # usememos list shape: a `memos` array whose every record carries the
        # written content (`content`); lists without that record shape stay out.
        # Guard mirrors the frontend: a non-array `memories` value keeps the
        # later list shapes reachable instead of short-circuiting them.
        if not isinstance(memories, list):
            memos = parsed.get(MEMOS_ARRAY_KEY)
            if isinstance(memos, list) and all(
                isinstance(item, dict) and isinstance(item.get("content"), str)
                for item in memos
            ):
                memories = memos
        # Current PowerContext lists use artifact-bearing `entries`. Empty
        # generic arrays need an explicit tool declaration to prove semantics.
        if not isinstance(memories, list):
            items = parsed.get("entries")
            if (
                isinstance(items, list)
                and (items or category == "list")
                and all(_powercontext_record(item) for item in items)
            ):
                memories = items
        # Generic `items` only carries memory meaning on a declared list
        # tool; arbitrary MCP lists must not become memory inventories.
        if not isinstance(memories, list) and category == "list":
            items = parsed.get("items")
            if isinstance(items, list) and all(
                isinstance(item, dict) and isinstance(item.get("text"), str)
                for item in items
            ):
                memories = items
        if isinstance(memories, list):
            entries = [item for item in memories if isinstance(item, dict)]
            states_known = len(entries) == len(memories) and all(
                isinstance(item.get("state"), str) and item["state"]
                for item in entries
            )
            # Visible states across the surveyed backends: PowerContext's
            # `active` and usememos' NORMAL/PINNED; ARCHIVED stays excluded.
            active = (
                sum(
                    str(item["state"]).strip().lower() in {"active", "normal", "pinned"}
                    for item in entries
                )
                if states_known
                else None
            )
            return {
                "type": "list",
                "hits": len(entries),
                "activeCount": active,
                "partial": any(
                    parsed.get(key)
                    for key in (
                        "next_cursor", "has_more", "nextPageToken", "next_page_token"
                    )
                ),
            }
        hits = parsed.get(HITS_ARRAY_KEY)
        if isinstance(hits, list) and (
            category == "search" or (hits and all(_memory_hit(hit) for hit in hits))
        ):
            # Structured hybrid-search: same search classification as the
            # text shape, entries extracted from each hit's memory record.
            return {
                "type": "search",
                "hits": len(hits),
                "entries": _hybrid_hit_entries(hits),
            }
        records = parsed.get("records")
        if (
            isinstance(records, list)
            and (records or category == "write")
            and all(_powercontext_record(record) for record in records)
        ):
            first = records[0] if records else {}
            return {
                "type": "write",
                "wroteId": first.get("artifact", {}).get("artifact_id"),
                "memoryKind": first.get("kind"),
                "state": first.get("state"),
                "writeEntries": [
                    {
                        "content": record["text"].strip(),
                        "memoryKind": record.get("kind"),
                        "state": record.get("state"),
                    }
                    for record in records
                    if record["text"].strip()
                ],
            }
        # usememos create shape: the created memo resource itself, carrying
        # the written content with the resource name and visibility.
        if (
            isinstance(parsed.get("content"), str)
            and isinstance(parsed.get("name"), str)
            and isinstance(parsed.get("visibility"), str)
        ):
            state = parsed.get("state")
            return {
                "type": "write",
                "wroteId": parsed["name"],
                "memoryKind": None,
                "state": state if isinstance(state, str) else None,
            }
    matched = MATCHED_RE.search(result_text)
    if matched:
        return {
            "type": "search",
            "hits": int(matched.group(1)),
            "entries": [
                {"state": state.strip(), "content": content.strip()}
                for state, content in HIT_LINE_RE.findall(result_text)
            ],
        }
    if FOUND_RE.search(result_text):
        entries = [
            {"state": "", "content": content.strip()}
            for content in FOUND_MEMORY_ITEM_RE.findall(result_text)
        ]
        if entries:
            # OpenViking find: hits are the memory items only, not the
            # itemized total (resource/skill items stay out of the count).
            return {"type": "search", "hits": len(entries), "entries": entries}
    stored = STORED_RE.search(result_text)
    if stored:
        return {
            "type": "write",
            "wroteId": stored.group(1),
            "memoryKind": stored.group(2) if stored.group(2) else None,
            "state": stored.group(3) if stored.group(3) else None,
        }
    wrote = WROTE_RE.search(result_text)
    if wrote:
        # OpenViking write result line; the URI is the written memory unit.
        return {
            "type": "write",
            "wroteId": wrote.group(1),
            "memoryKind": None,
            "state": None,
        }
    if result_text.startswith(MEMORY_NOTE_PREFIX):
        return {"type": "note"}
    return None


def _mcp_result_error(result: object) -> str | None:
    """None means no MCP error; an empty string means no reported reason."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, dict) or result.get("isError") is not True:
        return None
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        item["text"].strip()
        for item in content
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
        and item["text"].strip()
    )


def _declared_category(declared: object, tool_name: str) -> str | None:
    if not isinstance(declared, dict):
        return None
    for category, key in (
        ("search", "searchTools"),
        ("write", "writeTools"),
        ("list", "listTools"),
        ("manage", "manageTools"),
    ):
        tools = declared.get(key)
        if isinstance(tools, list) and tool_name in tools:
            return category
    return None


def _memory_call(
    span: dict, agent_product: str, declared_by_server: dict | None = None
) -> dict | None:
    """One MCP span as a memory-candidate call, mirroring the MCP parser but
    keeping the argument/result payload the memory metrics need."""
    info = _mcp_span_info(span)
    if info is None:
        return None
    server_id, tool_name = info
    span_status = span.get("status") if isinstance(span.get("status"), dict) else {}
    status_message = str(span_status.get("message") or "")
    start_time = _span_time(span.get("start_time_unix_nano"))
    end_time = _span_time(span.get("end_time_unix_nano"))
    start = start_time.timestamp() * 1_000 if start_time else 0.0
    end = end_time.timestamp() * 1_000 if end_time else 0.0
    attributes = span.get("attributes") or {}
    result_error = _mcp_result_error(attributes.get("gen_ai.tool.call.result"))
    outcome = tool_span_outcome(span)
    failed = outcome == "error"
    reported_duration = attributes.get("agentic.tool.duration_ms")
    try:
        duration_ms = float(reported_duration)
    except (TypeError, ValueError, OverflowError):
        duration_ms = None
    if duration_ms is not None and (
        isinstance(reported_duration, bool)
        or not math.isfinite(duration_ms)
        or duration_ms < 0
    ):
        duration_ms = None
    if (
        duration_ms is None
        and start_time
        and end_time
        and end >= start
    ):
        duration_ms = end - start

    args_raw = attributes.get("gen_ai.tool.call.arguments")
    args: dict = {}
    if isinstance(args_raw, str) and args_raw.strip():
        try:
            parsed = json.loads(args_raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            args = parsed
    elif isinstance(args_raw, dict):
        args = args_raw
    category = _declared_category((declared_by_server or {}).get(server_id), tool_name)
    evidence = _memory_evidence(attributes.get("gen_ai.tool.call.result"), category)
    # Identity uses this call's evidence, not another tool on the same server.
    # Keep proven identity on failed calls while excluding their result metrics.
    is_memory = (
        server_id in (declared_by_server or {})
        or str(attributes.get("agentic.tool.category") or "").strip().lower() == "memory"
        or evidence is not None
    )
    if failed:
        evidence = None
    if evidence and evidence["type"] == "list" and any(
        args.get(key) for key in ("cursor", "offset", "pageToken", "page_token")
    ):
        evidence["partial"] = True
    return {
        "serverId": server_id,
        "toolName": tool_name,
        "traceId": str(span.get("trace_id") or ""),
        "spanId": str(span.get("span_id") or ""),
        "agentProduct": str(span.get("agent_product") or agent_product or "").strip(),
        "status": outcome,
        "durationMs": round(duration_ms) if duration_ms is not None else None,
        "startMs": max(0, start),
        "timestamp": datetime.fromtimestamp(max(0, start) / 1000, tz=UTC)
        .isoformat()
        .replace("+00:00", "Z")
        if start_time
        else "",
        "lastError": (result_error or status_message or "") if failed else "",
        "argsSummary": _summarize_arguments(args_raw),
        "args": args,
        "evidence": evidence,
        "isMemory": is_memory,
    }


def _empty_server(server_id: str, declared: bool, description: str) -> dict:
    return {
        "serverId": server_id,
        "declared": declared,
        "description": description,
        "sourceAgents": [],
        "calls": [],
        "tools": {},
        "entries": {},
        "latestList": None,
    }


def _category_of(call: dict, declared: object, tool_name: str) -> str | None:
    if "nativeCategory" in call:
        return call["nativeCategory"]
    category = _declared_category(declared, tool_name)
    if category:
        return category
    evidence = call["evidence"]
    if evidence and evidence["type"] in {"search", "write", "list"}:
        return evidence["type"]
    return None


def _has_known_native_search_outcome(call: dict) -> bool:
    # A reported failure contributes an attempt without a returned result.
    return call["evidence"] is not None or call.get("nativeSearchFailed", False)


def _scan_memory_window(
    spans: list[dict], declared_by_server: dict[str, dict], agent_product: str = ""
) -> dict:
    """Aggregate one complete span window into the memory observability scan.

    Declared servers count in full; undeclared calls join only with their own
    explicit memory category or verified memory result shape. Evidence
    metrics (hits, entries, entry count) only come from spans whose result
    parses — unknown shapes degrade to plain call counts.
    """
    memory_servers: dict[str, dict | None] = {}
    calls: list[dict] = []
    from .native_memory import native_memory_calls
    native_calls = native_memory_calls(spans, agent_product)
    for span in spans:
        call = _memory_call(span, agent_product, declared_by_server)
        if call is None or not call["isMemory"]:
            continue
        calls.append(call)
        server_id = call["serverId"]
        if server_id in memory_servers:
            continue
        if server_id in declared_by_server:
            memory_servers[server_id] = declared_by_server[server_id]
        else:
            memory_servers[server_id] = None

    for call in native_calls:
        calls.append(call)
        memory_servers.setdefault(call['serverId'], None)

    native_results_complete = all(
        _has_known_native_search_outcome(call)
        for call in native_calls
        if call["nativeCategory"] == "search"
    )
    entries: dict[str, dict] = {}
    search_durations: list[int] = []
    aggregated: dict[str, dict] = {}
    for call in calls:
        server_id = call["serverId"]
        if server_id not in memory_servers:
            continue
        if server_id not in aggregated:
            aggregated[server_id] = _empty_server(
                server_id, server_id in declared_by_server, ""
            )
        server = aggregated[server_id]
        if call["agentProduct"] and call["agentProduct"] not in server["sourceAgents"]:
            server["sourceAgents"].append(call["agentProduct"])
        server["calls"].append(call)

        tool_name = call["toolName"]
        category = _category_of(call, memory_servers[server_id], tool_name)
        call["category"] = category
        tool = server["tools"].setdefault(
            tool_name,
            {
                "toolName": tool_name,
                "transport": call.get("transport", "mcp"),
                "category": category,
                "calls": 0,
                "statuses": [],
                "durations": [],
                "lastError": "",
                "lastErrorMs": -1,
            },
        )
        tool["calls"] += 1
        tool["statuses"].append(call["status"])
        if call["durationMs"] is not None:
            tool["durations"].append(call["durationMs"])
        if call["status"] == "error":
            if call["startMs"] >= tool["lastErrorMs"]:
                tool["lastError"] = call["lastError"]
                tool["lastErrorMs"] = call["startMs"]

        search_duration = call.get("searchDurationMs", call["durationMs"])
        if category == "search" and search_duration is not None:
            search_durations.append(search_duration)
        evidence = call["evidence"]
        if not evidence:
            continue
        if category == "search" and evidence["type"] == "search":
            seen_contents: set[str] = set()
            for entry in evidence["entries"]:
                if entry["content"] in seen_contents:
                    continue
                seen_contents.add(entry["content"])
                for target in (entries, server["entries"]):
                    record = target.setdefault(
                        entry["content"],
                        {
                            "content": entry["content"],
                            "kind": None,
                            "state": None,
                            "reuses": 0,
                            "lastUsedMs": call["startMs"],
                            "lastTraceId": call["traceId"],
                            "lastSpanId": call["spanId"],
                        },
                    )
                    record["reuses"] += 1
                    if call["startMs"] >= record["lastUsedMs"]:
                        record["state"] = entry["state"]
                        record["lastUsedMs"] = call["startMs"]
                        record["lastTraceId"] = call["traceId"]
                        record["lastSpanId"] = call["spanId"]
        elif category == "list" and evidence["type"] == "list":
            latest = server["latestList"]
            if latest is None or call["startMs"] >= latest[0]:
                server["latestList"] = (
                    call["startMs"],
                    {
                        "activeCount": evidence["activeCount"],
                        "returnedCount": evidence["hits"],
                        "partial": evidence.get("partial", False),
                        "timestamp": call["timestamp"],
                        "traceId": call["traceId"],
                        "spanId": call["spanId"],
                        "toolName": call["toolName"],
                        "argsSummary": call["argsSummary"],
                    },
                )
        elif evidence["type"] == "write":
            written = evidence.get("writeEntries")
            if written is None:
                written = [{"content": _written_content(call), **evidence}]
            for entry in written:
                content = entry["content"]
                if not content:
                    continue
                for target in (entries, server["entries"]):
                    record = target.setdefault(
                        content,
                        {
                            "content": content,
                            "kind": None,
                            "state": None,
                            "reuses": 0,
                            "lastUsedMs": call["startMs"],
                            "lastTraceId": call["traceId"],
                            "lastSpanId": call["spanId"],
                        },
                    )
                    record["kind"] = entry.get("memoryKind") or record["kind"]
                    if call["startMs"] >= record["lastUsedMs"]:
                        record["state"] = entry.get("state") or record["state"]
                        record["lastUsedMs"] = call["startMs"]
                        record["lastTraceId"] = call["traceId"]
                        record["lastSpanId"] = call["spanId"]

    serialized_servers = []
    all_search_hits = 0
    all_search_calls = 0
    all_search_hit_calls = 0
    all_search_result_calls = 0
    all_search_content_calls = 0
    for server_id, server in aggregated.items():
        category_counts: dict[str, dict[str, int]] = {}
        for call in server["calls"]:
            category = call["category"]
            if category:
                counts = category_counts.setdefault(call["toolName"], {})
                counts[category] = counts.get(category, 0) + 1
        for tool in server["tools"].values():
            counts = category_counts.get(tool["toolName"])
            if counts:
                tool["category"] = max(counts, key=counts.get)
            tool.pop("lastErrorMs")
            durations = tool.pop("durations")
            calls_count = tool.pop("calls")
            tool["stats"] = {
                "calls": calls_count,
                "durationSamples": len(durations),
                **call_status_stats(tool.pop("statuses")),
                **latency_stats(durations, empty=None),
                "lastError": tool.pop("lastError"),
            }
        server_calls = server["calls"]
        # Use complete canonical calls, not the bounded recent-call preview.
        # Only native_memory_calls adds nativeCategory; MCP over HTTP stays MCP.
        call_kinds = {
            "native" if "nativeCategory" in call else "mcp" for call in server_calls
        }
        durations = [
            call["durationMs"]
            for call in server_calls
            if call["durationMs"] is not None
        ]
        search_calls = [
            call
            for call in server_calls
            if call["category"] == "search"
        ]
        search_results = [
            call
            for call in search_calls
            if call["evidence"] and call["evidence"]["type"] == "search"
        ]
        total_hits = sum(
            call["evidence"]["hits"] or 0
            for call in search_results
            if call["evidence"]["hits"] is not None
        )
        hit_calls = sum(
            1
            for call in search_results
            if call["evidence"]["hits"] is not None and call["evidence"]["hits"] > 0
        )
        content_calls = sum(
            len(call["evidence"]["entries"]) == call["evidence"]["hits"]
            for call in search_results
        )
        search_duration_samples = sum(
            call.get("searchDurationMs", call["durationMs"]) is not None for call in search_calls
        )
        all_search_calls += len(search_calls)
        all_search_hits += total_hits
        all_search_hit_calls += hit_calls
        all_search_result_calls += len(search_results)
        all_search_content_calls += content_calls
        native_search_complete = all(
            _has_known_native_search_outcome(call)
            for call in search_calls
            if "nativeCategory" in call
        )
        recent = sorted(server_calls, key=lambda call: -call["startMs"])[
            :MAX_RECENT_CALLS
        ]
        recent_rows = []
        for call in recent:
            evidence = call.pop("evidence") or {}
            call["outcomeKind"] = evidence.get("type", "")
            call["hits"] = evidence.get("hits")
            call["wroteId"] = evidence.get("wroteId")
            call.pop("startMs")
            recent_rows.append(_serialize_call_row(call))
        serialized_servers.append(
            {
                "serverId": server_id,
                "callKinds": [kind for kind in ("native", "mcp") if kind in call_kinds],
                "declared": server["declared"],
                "description": server["description"],
                "sourceAgents": server["sourceAgents"],
                "lastSeen": recent[0]["timestamp"] if recent else "",
                "stats": {
                    "calls": len(server_calls),
                    "durationSamples": len(durations),
                    **call_status_stats(call["status"] for call in server_calls),
                    **latency_stats(durations, empty=None),
                },
                "memoryStats": {
                    "searchCalls": len(search_calls),
                    "searchResultCalls": len(search_results),
                    "searchHitCalls": hit_calls,
                    "searchContentCalls": content_calls,
                    "searchDurationSamples": search_duration_samples,
                    "searchHitRate": round(hit_calls / len(search_calls), 4)
                    if search_calls and native_search_complete
                    else None,
                    "totalHits": total_hits,
                    "entryCount": server["latestList"][1]["activeCount"]
                    if server["latestList"]
                    else None,
                    "entryCountIsPartial": server["latestList"][1]["partial"]
                    if server["latestList"]
                    else False,
                },
                "entrySnapshot": server["latestList"][1]
                if server["latestList"]
                else None,
                "tools": sorted(
                    server["tools"].values(),
                    key=lambda tool: (-tool["stats"]["calls"], tool["toolName"]),
                ),
                "recentCalls": recent_rows,
                "entries": _serialize_memory_entries(server["entries"])[
                    :MAX_RECENT_CALLS
                ],
            }
        )
    serialized_servers.sort(key=lambda server: server["serverId"])

    entry_rows = _serialize_memory_entries(entries)
    entry_counts = [
        server["memoryStats"]["entryCount"]
        for server in serialized_servers
        if server["memoryStats"]["entryCount"] is not None
    ]

    return {
        "limits": {"entries": MAX_RECENT_CALLS, "recentCalls": MAX_RECENT_CALLS},
        "overview": {
            "serverCount": len(serialized_servers),
            "calledToolCount": sum(len(server["tools"]) for server in serialized_servers),
            "p95Ms": max(
                (
                    server["stats"]["p95Ms"]
                    for server in serialized_servers
                    if server["stats"]["p95Ms"] is not None
                ),
                default=None,
            ),
            "p50Ms": max(
                (
                    server["stats"]["p50Ms"]
                    for server in serialized_servers
                    if server["stats"]["p50Ms"] is not None
                ),
                default=None,
            ),
            "calls": sum(server["stats"]["calls"] for server in serialized_servers),
            **call_status_stats(call["status"] for call in calls),
            "searchCalls": all_search_calls,
            "searchResultCalls": all_search_result_calls,
            "searchHitCalls": all_search_hit_calls,
            "searchContentCalls": all_search_content_calls,
            "searchDurationSamples": len(search_durations),
            "searchHitRate": round(all_search_hit_calls / all_search_calls, 4)
            if all_search_calls and native_results_complete
            else None,
            "totalHits": all_search_hits,
            "reusedEntryCount": sum(1 for record in entry_rows if record["reuses"]),
            "searchP95Ms": latency_stats(search_durations, empty=None)["p95Ms"],
            "entryCount": sum(entry_counts) if entry_counts else None,
            "entryCountIsPartial": any(
                server["memoryStats"]["entryCountIsPartial"]
                for server in serialized_servers
            ),
            "scanned": len(spans),
            "availableTraces": len({span.get("trace_id") for span in spans}),
            "truncated": False,
        },
        "servers": serialized_servers,
        "entries": entry_rows[:MAX_RECENT_CALLS],
    }


def _serialize_memory_entries(entries: dict[str, dict]) -> list[dict]:
    rows = sorted(
        (dict(record) for record in entries.values()),
        key=lambda record: (-record["reuses"], -record["lastUsedMs"]),
    )
    for record in rows:
        last_used_ms = record.pop("lastUsedMs")
        record["lastUsedAt"] = datetime.fromtimestamp(
            last_used_ms / 1000, tz=UTC
        ).isoformat().replace("+00:00", "Z") if last_used_ms else ""
    return rows


def _written_content(call: dict) -> str:
    """The content a write call persisted, from the raw call arguments."""
    args = call.get("args")
    if isinstance(args, dict):
        content = args.get("content")
        if isinstance(content, str):
            return content
        # usememos create nests the content under `body`.
        body = args.get("body")
        if isinstance(body, dict) and isinstance(body.get("content"), str):
            return body["content"]
    return ""


def _serialize_call_row(call: dict) -> dict:
    """Public shape of one recent call (no internal sort keys)."""
    return {
        key: call[key]
        for key in (
            "timestamp",
            "toolName",
            "category",
            "argsSummary",
            "hits",
            "outcomeKind",
            "wroteId",
            "status",
            "durationMs",
            "traceId",
            "spanId",
            "agentProduct",
            "lastError",
            "transport",
            "executionState",
        ) if key in call
    }


def _collect_memory_spans(service, *, space, user, query_params: dict) -> dict:
    return collect_span_window(
        service.list_memory_spans,
        space=space,
        user=user,
        query_params=query_params,
        label="memory",
        page_size=MAX_MCP_SPAN_PAGE_SIZE,
        max_spans=MAX_MEMORY_SCAN_SPANS,
        limit_code=3202,
    )


def _declared_memory_semantics(
    space,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Per-server declared memory semantics and descriptions from the registry.

    Imported declarations win; the known memory products
    (BUILTIN_MEMORY_SERVER_DECLARATIONS) fill in server ids without an
    imported declaration so a fresh deployment recognizes them with zero
    configuration.
    """
    declared: dict[str, dict] = {}
    descriptions: dict[str, str] = {}
    rows = McpServerConfig.objects.filter(
        space=space, gmt_deleted__isnull=True, memory_semantics__isnull=False
    ).only("server_id", "memory_semantics", "description")
    for row in rows:
        semantics = row.memory_semantics
        if isinstance(semantics, dict) and any(
            key in semantics for key in MEMORY_SEMANTIC_KEYS
        ):
            declared[row.server_id] = semantics
            descriptions[row.server_id] = row.description or ""
    for server_id, semantics in BUILTIN_MEMORY_SERVER_DECLARATIONS.items():
        if server_id not in declared:
            declared[server_id] = semantics
            descriptions[server_id] = ""
    return declared, descriptions


class MemoryObservationView(APIView):
    """Aggregate memory operations from verified MCP and native spans."""

    permission_classes = [IsAuthenticated]

    def get(self, request, space_id: str):
        try:
            space = Space.objects.get(
                uuid=space_id, status=1, gmt_deleted__isnull=True
            )
        except Space.DoesNotExist:
            return error_response("Space not found", 3001, 404)
        denied = check_space_access_or_error(request.user, space)
        if denied:
            return denied

        from_ts = request.query_params.get("fromTimestamp", "")
        to_ts = request.query_params.get("toTimestamp", "")
        if not from_ts or not to_ts:
            return error_response("fromTimestamp and toTimestamp are required", 3201)
        agent_product = str(request.query_params.get("agent_product", "")).strip()
        agent_uuid = str(request.query_params.get("agent_uuid", "")).strip()

        declared_by_server, server_descriptions = _declared_memory_semantics(space)
        try:
            scan = _collect_memory_spans(
                get_default_trace_query_service(),
                space=space,
                user=request.user,
                query_params={
                    "from": from_ts,
                    "to": to_ts,
                    "page": 1,
                    "limit": MAX_MCP_SPAN_PAGE_SIZE,
                    **({"agent_product": agent_product} if agent_product else {}),
                    **({"agent_uuid": agent_uuid} if agent_uuid else {}),
                },
            )
        except TraceQueryServiceError as exc:
            logger.warning("Memory trace scan failed", exc_info=True)
            return error_response(exc.message, exc.code)
        except Exception:
            logger.warning("Memory trace scan request failed", exc_info=True)
            return error_response("Failed to scan memory traces", 3203)

        result = _scan_memory_window(
            scan["spans"],
            declared_by_server,
            agent_product,
        )
        for server in result["servers"]:
            server["description"] = server_descriptions.get(server["serverId"], "")
        result["evidence"] = {
            "source": "recorded_memory_spans",
            "complete": True,
            "queriedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "from": from_ts,
            "to": to_ts,
            "agentProduct": agent_product,
            "agentUuid": agent_uuid or None,
        }
        return success_response(result)

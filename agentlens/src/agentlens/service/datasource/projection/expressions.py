"""
Datadog-style expression engine for trace/span data.

Supports:
  - {{spans[*].name}}                                     wildcard fan-out
  - {{spans[0].name}}                                     index
  - {{spans[attributes.gen_ai.span.kind:LLM].name}}       filter
  - auto-parse JSON strings in attribute values
  - {{*}}                                                  entire payload as JSON
  - {{spans[name:filtering].children[*].name}}             tree navigation (opt-in: build_tree=True)

Usage:
    from agentlens.service.datasource.projection.expressions import preprocess, query, render
    data = preprocess(raw_trace_data)                           # flat, no tree
    data = preprocess(raw_trace_data, build_tree=True)          # with children[]
    result = render("{{spans[*].name}}", data)
"""

import json
import re
from typing import Any

# ── Preprocessing: build tree + parse JSON strings ──


def preprocess(data: dict, *, build_tree: bool = False) -> dict:
    """Enrich trace data:
    1. Auto-parse JSON string values in attributes
    2. Optionally build children[] on each span from parent_span_id (off by default)
    """
    data = json.loads(json.dumps(data))  # deep copy
    spans = data.get("spans", [])

    if build_tree:
        id_map = {s["span_id"]: s for s in spans}
        for s in spans:
            s["children"] = []
        for s in spans:
            pid = s.get("parent_span_id", "")
            if pid and pid in id_map:
                id_map[pid]["children"].append(s)

    # auto-parse JSON strings in nested and flattened OTel attributes
    for span in spans:
        attrs = span.get("attributes", {})
        if isinstance(attrs, dict):
            for k, v in attrs.items():
                if isinstance(v, str) and v.startswith(("[", "{")):
                    try:
                        attrs[k] = json.loads(v)
                    except (json.JSONDecodeError, ValueError):
                        pass
        for k, v in span.items():
            if k.startswith("attributes.") and isinstance(v, str) and v.startswith(("[", "{")):
                try:
                    span[k] = json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    pass

    return data


# ── Parser ──


def _parse_bracket(bracket_content: str) -> dict:
    """Parse bracket content. Supported forms:
    [*]         wildcard
    [0], [-1]   index (supports negative)
    [1,3]       inclusive range (Datadog-style, both ends included)
    [1:3]       Python-style slice (half-open: start included, end excluded)
    [1:]        slice from index to end
    [:3]        slice from start to index
    [:-1]       slice from start, excluding last element
    [-2:]       last N elements
    [key:val]   attribute filter
    """
    if bracket_content == "*":
        return {"wildcard": True}
    # Python-style slice with colon: [start:end], [start:], [:end], [:-1], [-2:]
    # Distinguish from filter: a slice has at most digits/minus/empty on each side
    if ":" in bracket_content:
        left, right = bracket_content.split(":", 1)
        left_s, right_s = left.strip(), right.strip()
        is_slice = (left_s == "" or re.fullmatch(r"-?\d+", left_s)) and (
            right_s == "" or re.fullmatch(r"-?\d+", right_s)
        )
        if is_slice:
            start = int(left_s) if left_s else None
            end = int(right_s) if right_s else None
            return {"slice": (start, end)}
        # otherwise it's a filter [key:val]
        return {"filter": (left_s, right_s)}
    # Datadog-style inclusive range [1,3]
    if "," in bracket_content:
        parts = bracket_content.split(",", 1)
        return {"range": (int(parts[0]), int(parts[1]))}
    # plain index [0], [-1], [-2]
    return {"index": int(bracket_content)}


def parse(expr: str) -> list[dict]:
    segments = []
    tokens = re.findall(r"[^.\[\]]+|\[[^\]]*\]", expr)
    for token in tokens:
        if token.startswith("[") and token.endswith("]"):
            segments.append(_parse_bracket(token[1:-1]))
        elif token:
            segments.append({"key": token})
    return segments


# ── Resolver ──


def _is_array_op(seg: dict) -> bool:
    """Check if a segment is an array operation (index/slice/range/wildcard)."""
    return any(k in seg for k in ("index", "slice", "range", "wildcard"))


def _match_longest_key(data: dict[str, Any], parts: list[str]) -> tuple[int, Any] | None:
    """Match the longest real key at the current source-data level."""
    for consumed in range(len(parts), 0, -1):
        key = ".".join(parts[:consumed])
        if key in data:
            return consumed, data[key]
    return None


def _get_nested(data: Any, dotted_key: str) -> Any:
    """Resolve dotted syntax without inventing objects absent from source data."""
    current = data
    remaining = dotted_key.split(".")
    while remaining:
        if not isinstance(current, dict):
            return None
        matched = _match_longest_key(current, remaining)
        if matched is None:
            return None
        consumed, current = matched
        remaining = remaining[consumed:]
    return current


def resolve(data: Any, segments: list[dict]) -> Any:
    if not segments:
        return data
    if data is None:
        return None

    seg, rest = segments[0], segments[1:]

    if "filter" in seg:
        key, val = seg["filter"]
        items = data if isinstance(data, list) else []
        matched = [i for i in items if str(_get_nested(i, key)) == val]
        # if next segment is an array op, apply to the matched list as a whole
        if rest and _is_array_op(rest[0]):
            return resolve(matched, rest)
        results = [resolve(m, rest) for m in matched]
        return _flatten(results)

    if "wildcard" in seg:
        items = data if isinstance(data, list) else []
        results = [resolve(i, rest) for i in items]
        return _flatten(results)

    if "range" in seg:
        start, end = seg["range"]
        items = data if isinstance(data, list) else []
        sliced = items[start : end + 1]  # Datadog inclusive range
        results = [resolve(i, rest) for i in sliced]
        return _flatten(results)

    if "slice" in seg:
        start, end = seg["slice"]
        items = data if isinstance(data, list) else []
        sliced = items[start:end]  # Python-style half-open slice
        results = [resolve(i, rest) for i in sliced]
        return _flatten(results)

    if "index" in seg:
        items = data if isinstance(data, list) else []
        idx = seg["index"]
        if -len(items) <= idx < len(items):
            return resolve(items[idx], rest)
        return None

    # plain key
    if isinstance(data, dict):
        key_parts = []
        for key_seg in segments:
            if "key" not in key_seg:
                break
            key_parts.append(key_seg["key"])
        matched = _match_longest_key(data, key_parts)
        if matched is None:
            return None
        consumed, val = matched
        rest = segments[consumed:]
    elif isinstance(data, list):
        # implicit fan-out: data is a list, fan out key access over each element
        results = [resolve(i, segments) for i in data]
        return _flatten(results)
    else:
        return None

    # implicit fan-out: only when next segment is a plain key
    if isinstance(val, list) and rest:
        next_seg = rest[0]
        if "key" in next_seg and len(next_seg) == 1:
            results = [resolve(i, rest) for i in val]
            return _flatten(results)

    return resolve(val, rest)


def _flatten(results: list) -> list | Any:
    flat = []
    for r in results:
        if isinstance(r, list):
            flat.extend(r)
        elif r is not None:
            flat.append(r)
    return flat


# ── Serializer ──


def serialize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            return ""
        if all(isinstance(v, str) for v in value):
            return "\n".join(value)
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, (dict, int, float, bool)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


# ── Template engine ──


def render(template: str, data: Any) -> str:
    def replacer(match: re.Match) -> str:
        expr = match.group(1).strip()
        if expr == "*":
            return json.dumps(data, ensure_ascii=False, indent=2)
        segments = parse(expr)
        result = resolve(data, segments)
        return serialize(result)

    return re.sub(r"\{\{(.+?)\}\}", replacer, template)


def query(expr: str, data: Any) -> Any:
    if expr.strip() == "*":
        return data
    segments = parse(expr.strip())
    return resolve(data, segments)

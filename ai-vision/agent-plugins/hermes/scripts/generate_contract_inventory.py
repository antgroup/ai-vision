#!/usr/bin/env python3
"""Generate the deterministic v4 semconv ↔ Weaver policy inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SEMCONV_DIR = REPO_ROOT / "docs/semconv"
POLICY_DIR = REPO_ROOT / "weaver/policies/aivision"
OUTPUT = REPO_ROOT / "hermes/tests/fixtures/semconv_policy_inventory_v4.json"
SEMCONV_FILES = ("attribute_groups.yaml", "entities.yaml", "events.yaml", "metrics.yaml", "spans.yaml")
RED_METRICS = (
    "agent.api_request.count",
    "agent.api_request.error.count",
    "agent.api_request.duration",
    "agent.tool_call.count",
    "agent.tool_call.error.count",
    "agent.tool_call.duration",
)
TRACE_POLICY_TO_SEMCONV = {
    "compaction": "span.ant.compaction",
    "llm": "span.ant.llm",
    "tool": "span.ant.tool",
    "turn": "span.ant.chat",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_groups(path: Path, group_prefix: str, name_field: str | None = None) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^  - id:\s+([^\s#]+)", line)
        if match:
            if current is not None:
                records.append(current)
            group_id = match.group(1)
            current = {"id": group_id} if group_id.startswith(group_prefix) else None
            continue
        if current is not None and name_field:
            match = re.match(rf"^    {re.escape(name_field)}:\s+([^\s#]+)", line)
            if match:
                current["name"] = match.group(1).strip('"\'')
    if current is not None:
        records.append(current)
    return sorted(records, key=lambda item: item["id"])


def _policy_inventory() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_entity: dict[str, dict[str, Any]] = defaultdict(lambda: {"ruleCount": 0, "files": set()})
    tree_hasher = hashlib.sha256()
    rego_files = sorted(POLICY_DIR.rglob("*.rego"))
    for path in rego_files:
        relative = path.relative_to(REPO_ROOT).as_posix()
        content = path.read_bytes()
        tree_hasher.update(relative.encode("utf-8") + b"\0" + content + b"\0")
        for entity in re.findall(rb"# @weaver\.rule\.entity:\s*([^\r\n]+)", content):
            entity_name = entity.decode("utf-8").strip()
            by_entity[entity_name]["ruleCount"] += 1
            by_entity[entity_name]["files"].add(relative)
    entities = [
        {"entity": entity, "ruleCount": details["ruleCount"], "files": sorted(details["files"])}
        for entity, details in sorted(by_entity.items())
    ]
    return entities, {"fileCount": len(rego_files), "sha256": tree_hasher.hexdigest()}


def build_inventory() -> dict[str, Any]:
    events = _parse_groups(SEMCONV_DIR / "events.yaml", "event.", "name")
    spans = _parse_groups(SEMCONV_DIR / "spans.yaml", "span.")
    metrics = _parse_groups(SEMCONV_DIR / "metrics.yaml", "metric.", "metric_name")
    policy_entities, policy_tree = _policy_inventory()

    event_names = {item.get("name", item["id"].removeprefix("event.ant.")) for item in events}
    span_ids = {item["id"] for item in spans}
    metric_names = {item.get("name", item["id"].removeprefix("metric.")) for item in metrics}

    event_policy_names = sorted(
        path.name for path in (POLICY_DIR / "events").iterdir()
        if path.is_dir() and path.name not in {"base", "common_attributes", "resource_attributes"}
    )
    mappings: list[dict[str, Any]] = [
        {"policyEntity": f"event:{name}", "semconvEntity": name, "registered": name in event_names}
        for name in event_policy_names
    ]
    mappings.extend(
        {"policyEntity": f"span:{name}", "semconvEntity": semconv_id, "registered": semconv_id in span_ids}
        for name, semconv_id in sorted(TRACE_POLICY_TO_SEMCONV.items())
    )
    mappings.extend([
        {
            "policyEntity": "metric:api_request" if ".api_request." in name else "metric:tool",
            "semconvEntity": name,
            "registered": name in metric_names,
        }
        for name in RED_METRICS
    ])

    source_files = []
    for name in SEMCONV_FILES:
        path = SEMCONV_DIR / name
        source_files.append({"path": path.relative_to(REPO_ROOT).as_posix(), "sha256": _sha256(path.read_bytes())})

    return {
        "schemaVersion": "semconv-policy-inventory.v1",
        "contract": "repository-current docs/semconv plus weaver-guard v4.x",
        "generatedBy": "hermes/scripts/generate_contract_inventory.py",
        "sources": {"semconv": source_files, "policyTree": policy_tree},
        "semconv": {"events": events, "spans": spans, "metrics": metrics},
        "policies": {"entities": policy_entities},
        "reconciliation": {
            "mappings": mappings,
            "missingSemconvForPolicy": [item for item in mappings if not item["registered"]],
            "normativeRedMetrics": list(RED_METRICS),
            "missingNormativeRedMetrics": sorted(set(RED_METRICS) - metric_names),
            "documentationOnlySemconv": {
                "events": sorted(event_names - set(event_policy_names)),
                "spans": sorted(span_ids - set(TRACE_POLICY_TO_SEMCONV.values())),
                "metrics": sorted(metric_names - set(RED_METRICS)),
            },
        },
    }


def render_inventory() -> str:
    return json.dumps(build_inventory(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_inventory()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale contract inventory: run python3 {Path(__file__).relative_to(REPO_ROOT)}")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()

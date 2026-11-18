#!/usr/bin/env python3
"""Generate and validate AgentLens object/topic ADR archives."""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs" / "adr"
README = ADR_DIR / "README.md"

OBJECTS_START = "<!-- adr-objects:start -->"
OBJECTS_END = "<!-- adr-objects:end -->"
INDEX_START = "<!-- adr-index:start -->"
INDEX_END = "<!-- adr-index:end -->"
RELATED_START = "<!-- adr-related:start -->"
RELATED_END = "<!-- adr-related:end -->"

FULL_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}\+08:00$")
OBJECT_ROW_RE = re.compile(
    r"^\| `(?P<key>[^`]+)` \| \[(?P<label>[^]]+)]\((?P<path>[^)]+)\) "
    r"\| (?P<scope>.*?) \|$"
)
FRONTMATTER_RE = re.compile(r"\A---\n(?P<yaml>.*?)\n---\n", re.DOTALL)
H2_RE = re.compile(r"^## (?!#)(?P<title>.+)$")
H1_RE = re.compile(r"^# (?!#)(?P<title>.+)$")
H3_RE = re.compile(r"^### (?!#)(?P<title>.+)$")
KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RECORD_HEADING_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*@\d{8}-\d{4}$")
RECORD_TITLE_RE = re.compile(r"^\*\*(?P<title>.+)\*\*$")
EXPLICIT_ANCHOR_RE = re.compile(r"<a\b[^>]*?\b(?:id|name)\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.I)
HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<title>.+?)\s*$")

REQUIRED_RECORD_FIELDS = {
    "record_id",
    "decision_status",
    "implementation_status",
    "proposed_at",
    "decided_at",
    "implemented_at",
    "supersedes",
    "affected_objects",
    "canonical_doc",
}
FORBIDDEN_RECORD_FIELDS = {
    "topic",
    "primary_object",
    "legacy_id",
    "baseline",
    "record_kind",
}
DECISION_STATUSES = {"proposed", "accepted", "rejected", "superseded"}
IMPLEMENTATION_STATUSES = {"not_started", "partial", "implemented", "retired"}
REQUIRED_RECORD_SECTIONS = (
    "Background",
    "Decision",
    "Unchanged",
    "Impact and risks",
    "Validation",
    "Implementation notes",
)
LEGACY_RECORD_SECTIONS = {
    "背景": "Background",
    "决策": "Decision",
    "保持不变": "Unchanged",
    "影响与风险": "Impact and risks",
    "验证": "Validation",
    "实施说明": "Implementation notes",
}


@dataclass(frozen=True)
class ObjectRegistration:
    key: str
    label: str
    path: str
    scope: str


@dataclass
class Record:
    object_key: str
    topic: str
    path: Path
    heading: str
    title: str
    metadata: dict[str, Any]

    @property
    def record_id(self) -> str:
        return str(self.metadata.get("record_id", ""))


@dataclass
class ObjectArchive:
    registration: ObjectRegistration
    path: Path
    metadata: dict[str, Any]
    text: str
    records: list[Record]


@dataclass(frozen=True)
class MarkdownLine:
    number: int
    text: str
    outside_fence: bool


def scalar_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def extract_between(text: str, start: str, end: str) -> str:
    try:
        return text.split(start, 1)[1].split(end, 1)[0].strip("\n")
    except IndexError as exc:
        raise ValueError(f"missing marker pair: {start} / {end}") from exc


def replace_between(text: str, start: str, end: str, body: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(text):
        raise ValueError(f"missing marker pair: {start} / {end}")
    replacement = f"{start}\n{body.rstrip()}\n{end}"
    return pattern.sub(lambda _: replacement, text, count=1)


def markdown_lines(text: str, *, source: str) -> list[MarkdownLine]:
    lines: list[MarkdownLine] = []
    fence_char = ""
    fence_length = 0
    fence_start = 0
    for number, raw_line in enumerate(text.splitlines(), start=1):
        outside_fence = not fence_char
        if fence_char:
            if re.fullmatch(
                rf"\s{{0,3}}{re.escape(fence_char)}{{{fence_length},}}\s*",
                raw_line,
            ):
                fence_char = ""
                fence_length = 0
            outside_fence = False
        else:
            opening = re.match(r"^\s{0,3}(?P<fence>`{3,}|~{3,})", raw_line)
            if opening:
                fence = opening.group("fence")
                fence_char = fence[0]
                fence_length = len(fence)
                fence_start = number
                outside_fence = False
        lines.append(MarkdownLine(number, raw_line, outside_fence))
    if fence_char:
        raise ValueError(f"{source}:{fence_start}: unclosed Markdown fence")
    return lines


def markdown_slug(title: str) -> str:
    title = re.sub(r"!\[([^]]*)]\([^)]+\)", r"\1", title)
    title = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", title)
    title = re.sub(r"<[^>]+>", "", title)
    title = title.replace("`", "").lower()
    title = "".join(char for char in title if char.isalnum() or char in {" ", "-", "_"})
    return re.sub(r"\s+", "-", title).strip("-")


def markdown_anchors(text: str, *, source: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = defaultdict(int)
    for line in markdown_lines(text, source=source):
        if not line.outside_fence:
            continue
        anchors.update(EXPLICIT_ANCHOR_RE.findall(line.text))
        heading = HEADING_RE.match(line.text)
        if not heading:
            continue
        title = re.sub(r"\s+#+\s*$", "", heading.group("title"))
        base = markdown_slug(title)
        if not base:
            continue
        count = counts[base]
        anchors.add(base if count == 0 else f"{base}-{count}")
        counts[base] += 1
    return anchors


def parse_frontmatter(path: Path, text: str) -> dict[str, Any]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"{path.relative_to(ROOT)}: missing YAML front matter")
    metadata = yaml.safe_load(match.group("yaml")) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"{path.relative_to(ROOT)}: front matter must be a YAML mapping")
    return metadata


def load_registry() -> list[ObjectRegistration]:
    block = extract_between(README.read_text(encoding="utf-8"), OBJECTS_START, OBJECTS_END)
    lines = block.splitlines()
    expected_headers = (
        ["| Object key | Object archive | Scope |", "|---|---|---|"],
        ["| 对象键 | 对象档案 | 覆盖范围 |", "|---|---|---|"],
    )
    if lines[:2] not in expected_headers:
        raise ValueError("docs/adr/README.md: invalid ADR object registry header")
    registrations: list[ObjectRegistration] = []
    for line in lines[2:]:
        match = OBJECT_ROW_RE.fullmatch(line)
        if not match:
            raise ValueError(f"docs/adr/README.md: invalid ADR object registry row: {line}")
        registration = ObjectRegistration(**match.groupdict())
        if not KEY_RE.fullmatch(registration.key):
            raise ValueError(f"docs/adr/README.md: invalid ADR object key: {registration.key!r}")
        expected_path = f"{registration.key}/README.md"
        if registration.path != expected_path:
            raise ValueError(f"docs/adr/README.md: {registration.key} path must be {expected_path}")
        if not registration.scope.strip():
            raise ValueError(f"docs/adr/README.md: {registration.key} scope must be non-empty")
        registrations.append(registration)
    if not registrations:
        raise ValueError("ADR object registry has no entries")
    return registrations


def parse_topic_file(path: Path, object_key: str) -> list[Record]:
    text = path.read_text(encoding="utf-8")
    metadata = parse_frontmatter(path, text)
    required = {"object_key", "topic", "title"}
    if set(metadata) != required:
        raise ValueError(
            f"{path.relative_to(ROOT)}: topic front matter must contain only "
            "object_key, topic, and title"
        )
    topic = str(metadata.get("topic", ""))
    if metadata.get("object_key") != object_key:
        raise ValueError(f"{path.relative_to(ROOT)}: object_key must be {object_key!r}")
    if not KEY_RE.fullmatch(topic):
        raise ValueError(f"{path.relative_to(ROOT)}: invalid topic key {topic!r}")
    if path.name != f"{topic}.md":
        raise ValueError(f"{path.relative_to(ROOT)}: filename must be {topic}.md")
    if not isinstance(metadata.get("title"), str) or not metadata["title"]:
        raise ValueError(f"{path.relative_to(ROOT)}: title must be non-empty")

    lines = markdown_lines(text, source=str(path.relative_to(ROOT)))
    h1_lines = [line for line in lines if line.outside_fence and H1_RE.fullmatch(line.text)]
    if len(h1_lines) != 1:
        raise ValueError(f"{path.relative_to(ROOT)}: topic file must contain exactly one H1")
    h1 = H1_RE.fullmatch(h1_lines[0].text)
    if h1 is None:
        raise AssertionError("validated H1 did not match")
    if h1.group("title") != metadata["title"]:
        raise ValueError(f"{path.relative_to(ROOT)}: H1 must match the front matter title")
    headings = [
        index
        for index, line in enumerate(lines)
        if line.outside_fence and H2_RE.fullmatch(line.text)
    ]
    if not headings:
        raise ValueError(f"{path.relative_to(ROOT)}: topic file has no decision records")

    records: list[Record] = []
    for position, heading_index in enumerate(headings):
        heading_line = lines[heading_index]
        heading = H2_RE.fullmatch(heading_line.text)
        if heading is None:
            raise AssertionError("validated heading did not match")
        record_heading = heading.group("title")
        if not RECORD_HEADING_RE.fullmatch(record_heading):
            raise ValueError(
                f"{path.relative_to(ROOT)}:{heading_line.number}: "
                "record heading must use <object>-<topic>@YYYYMMDD-HHMM"
            )
        section_end = headings[position + 1] if position + 1 < len(headings) else len(lines)
        title_index = heading_index + 2
        yaml_start = heading_index + 4
        if (
            heading_index + 1 >= section_end
            or lines[heading_index + 1].text != ""
            or title_index >= section_end
            or not RECORD_TITLE_RE.fullmatch(lines[title_index].text)
            or title_index + 1 >= section_end
            or lines[title_index + 1].text != ""
            or yaml_start >= section_end
            or lines[yaml_start].text != "```yaml"
        ):
            raise ValueError(
                f"{path.relative_to(ROOT)}:{heading_line.number}: "
                "record heading must be followed by a bold title and YAML metadata"
            )
        yaml_end = next(
            (index for index in range(yaml_start + 1, section_end) if lines[index].text == "```"),
            None,
        )
        if yaml_end is None:
            raise ValueError(
                f"{path.relative_to(ROOT)}:{lines[yaml_start].number}: "
                "unclosed ADR YAML metadata block"
            )
        record_metadata = (
            yaml.safe_load(
                "\n".join(lines[index].text for index in range(yaml_start + 1, yaml_end))
            )
            or {}
        )
        if not isinstance(record_metadata, dict):
            raise ValueError(
                f"{path.relative_to(ROOT)}:{lines[yaml_start].number}: "
                "ADR metadata must be a YAML mapping"
            )
        section_titles = [
            match.group("title")
            for line in lines[yaml_end + 1 : section_end]
            if line.outside_fence and (match := H3_RE.fullmatch(line.text)) is not None
        ]
        required_sections = [
            normalized
            for title in section_titles
            if (normalized := LEGACY_RECORD_SECTIONS.get(title, title)) in REQUIRED_RECORD_SECTIONS
        ]
        if required_sections != list(REQUIRED_RECORD_SECTIONS):
            raise ValueError(
                f"{path.relative_to(ROOT)}:{heading_line.number}: record must contain "
                "the required sections in template order"
            )
        title_match = RECORD_TITLE_RE.fullmatch(lines[title_index].text)
        if title_match is None:
            raise AssertionError("validated record title did not match")
        records.append(
            Record(
                object_key=object_key,
                topic=topic,
                path=path,
                heading=record_heading,
                title=title_match.group("title"),
                metadata=record_metadata,
            )
        )
    return records


def load_all() -> tuple[list[ObjectRegistration], dict[str, ObjectArchive]]:
    registrations = load_registry()
    archives: dict[str, ObjectArchive] = {}
    for registration in registrations:
        path = ADR_DIR / registration.path
        text = path.read_text(encoding="utf-8")
        metadata = parse_frontmatter(path, text)
        records: list[Record] = []
        for topic_path in sorted(path.parent.glob("*.md")):
            if topic_path.name != "README.md":
                records.extend(parse_topic_file(topic_path, registration.key))
        archives[registration.key] = ObjectArchive(
            registration=registration,
            path=path,
            metadata=metadata,
            text=text,
            records=records,
        )
    return registrations, archives


def heading_for(record_id: str) -> str:
    return record_id.lower().replace("/", "-")


def record_stamp(value: Any) -> str:
    text = scalar_text(value)
    if not FULL_TIME_RE.fullmatch(text):
        return ""
    return text[:16].replace("-", "").replace("T", "-").replace(":", "")


def parse_timestamp(value: Any) -> datetime | None:
    text = scalar_text(value)
    if not text:
        return None
    if not FULL_TIME_RE.fullmatch(text):
        raise ValueError(text)
    parsed = datetime.fromisoformat(text)
    utc_offset = parsed.utcoffset()
    if utc_offset is None or utc_offset.total_seconds() != 8 * 3600:
        raise ValueError(text)
    return parsed


def validate_canonical_doc(value: Any, location: str) -> list[str]:
    if not isinstance(value, str) or not value:
        return [f"{location}: canonical_doc must be a non-empty path"]
    canonical_path, separator, raw_anchor = value.partition("#")
    path = (ROOT / canonical_path).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return [f"{location}: canonical_doc must stay inside the repository"]
    if not path.is_file():
        return [f"{location}: canonical_doc does not exist: {canonical_path}"]
    if not separator:
        return []
    anchor = unquote(raw_anchor)
    if not anchor:
        return [f"{location}: canonical_doc anchor must not be empty"]
    if path.suffix.lower() != ".md":
        return [f"{location}: canonical_doc anchors require Markdown"]
    try:
        anchors = markdown_anchors(path.read_text(encoding="utf-8"), source=canonical_path)
    except ValueError as exc:
        return [f"{location}: canonical_doc is malformed: {exc}"]
    if anchor not in anchors:
        return [f"{location}: canonical_doc anchor does not exist: {value}"]
    return []


def record_link(record: Record, *, from_object: str) -> str:
    fragment = markdown_slug(record.heading)
    if record.object_key == from_object:
        target = f"{record.path.name}#{fragment}"
    else:
        relative = record.path.relative_to(ADR_DIR).as_posix()
        target = f"../{relative}#{fragment}"
    return f"[{record.record_id}]({target})"


def current_records(records: list[Record], topic: str) -> tuple[list[Record], list[Record]]:
    topic_records = [record for record in records if record.topic == topic]
    targets = [
        record for record in topic_records if record.metadata.get("decision_status") == "accepted"
    ]
    implemented = [
        record
        for record in topic_records
        if record.metadata.get("implementation_status") == "implemented"
    ]
    return targets, implemented


def render_index(archive: ObjectArchive) -> str:
    if not archive.records:
        return "当前没有已登记主题。"
    lines = ["| 决策主题 | 目标决策 | 当前已实施决策 |", "|---|---|---|"]
    for topic in sorted({record.topic for record in archive.records}):
        targets, implemented = current_records(archive.records, topic)
        target_text = (
            "<br>".join(
                record_link(record, from_object=archive.registration.key) for record in targets
            )
            or "-"
        )
        implemented_text = (
            "<br>".join(
                record_link(record, from_object=archive.registration.key) for record in implemented
            )
            or "-"
        )
        lines.append(f"| [{topic}]({topic}.md) | {target_text} | {implemented_text} |")
    return "\n".join(lines)


def render_related(
    archive: ObjectArchive,
    registrations: list[ObjectRegistration],
    records_by_id: dict[str, Record],
) -> str:
    rows: dict[tuple[str, str], tuple[str, Record]] = {}
    object_key = archive.registration.key
    for record in records_by_id.values():
        if record.object_key != object_key and object_key in (
            record.metadata.get("affected_objects") or []
        ):
            rows[("affected", record.record_id)] = ("affected", record)
        related_ids = record.metadata.get("related_records") or []
        if record.object_key == object_key:
            for related_id in related_ids:
                related = records_by_id.get(str(related_id))
                if related is not None and related.object_key != object_key:
                    rows[("related", related.record_id)] = ("related", related)
        elif any(
            records_by_id.get(str(related_id)) is not None
            and records_by_id[str(related_id)].object_key == object_key
            for related_id in related_ids
        ):
            rows[("related", record.record_id)] = ("related", record)
    if not rows:
        return "当前没有由其他对象承载的关联决策。"

    labels = {registration.key: registration.label for registration in registrations}
    current_label = archive.registration.label
    lines = [
        "`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。",
        "",
        "| 对象关系 | 决策记录 | 标题 |",
        "|---|---|---|",
    ]
    for _, (relation, record) in sorted(rows.items()):
        link = record_link(record, from_object=object_key)
        other_label = labels[record.object_key]
        relation_text = (
            f"{other_label} → {current_label}"
            if relation == "affected"
            else f"{other_label} ↔ {current_label}"
        )
        lines.append(f"| {relation_text} | {link} | {record.title} |")
    return "\n".join(lines)


def rendered_readmes(
    registrations: list[ObjectRegistration],
    archives: dict[str, ObjectArchive],
) -> dict[Path, str]:
    records_by_id = {
        record.record_id: record for archive in archives.values() for record in archive.records
    }
    rendered: dict[Path, str] = {}
    for registration in registrations:
        archive = archives[registration.key]
        text = replace_between(archive.text, INDEX_START, INDEX_END, render_index(archive))
        text = replace_between(
            text,
            RELATED_START,
            RELATED_END,
            render_related(archive, registrations, records_by_id),
        )
        rendered[archive.path] = text
    return rendered


def generate_indexes(*, write: bool) -> list[Path]:
    registrations, archives = load_all()
    rendered = rendered_readmes(registrations, archives)
    changed = [path for path, text in rendered.items() if path.read_text(encoding="utf-8") != text]
    if write:
        for path in changed:
            path.write_text(rendered[path], encoding="utf-8")
    return changed


def validate_record(
    record: Record,
    registered_keys: set[str],
    records_by_id: dict[str, Record],
) -> list[str]:
    location = f"{record.path.relative_to(ROOT)}#{markdown_slug(record.heading)}"
    metadata = record.metadata
    errors: list[str] = []
    missing = sorted(REQUIRED_RECORD_FIELDS - set(metadata))
    if missing:
        errors.append(f"{location}: missing fields: {', '.join(missing)}")
        return errors
    forbidden = sorted(FORBIDDEN_RECORD_FIELDS & set(metadata))
    if forbidden:
        errors.append(f"{location}: forbidden fields: {', '.join(forbidden)}")

    decision_status = metadata.get("decision_status")
    implementation_status = metadata.get("implementation_status")
    if decision_status not in DECISION_STATUSES:
        errors.append(f"{location}: invalid decision_status {decision_status!r}")
    if implementation_status not in IMPLEMENTATION_STATUSES:
        errors.append(f"{location}: invalid implementation_status {implementation_status!r}")

    timestamps: dict[str, datetime | None] = {}
    for field in ("proposed_at", "decided_at", "implemented_at"):
        try:
            timestamps[field] = parse_timestamp(metadata.get(field))
        except (TypeError, ValueError):
            errors.append(f"{location}: {field} must be a real RFC 3339 minute at +08:00 or null")
            timestamps[field] = None
    proposed = timestamps["proposed_at"]
    decided = timestamps["decided_at"]
    implemented = timestamps["implemented_at"]
    if proposed is None:
        errors.append(f"{location}: proposed_at is required")
    if decision_status in {"accepted", "rejected", "superseded"} and decided is None:
        errors.append(f"{location}: decided_at is required for {decision_status}")
    if decision_status == "proposed" and decided is not None:
        errors.append(f"{location}: proposed records must not set decided_at")
    if decision_status in {"proposed", "rejected"} and implementation_status != "not_started":
        errors.append(
            f"{location}: {decision_status} records must have implementation_status not_started"
        )
    if implementation_status == "implemented" and implemented is None:
        errors.append(f"{location}: implemented_at is required for implemented records")
    if implementation_status in {"not_started", "partial"} and implemented is not None:
        errors.append(f"{location}: unfinished records must not set implemented_at")
    if implementation_status == "retired" and implemented is None:
        errors.append(f"{location}: retired records must retain implemented_at")
    if implementation_status == "retired" and decision_status != "superseded":
        errors.append(f"{location}: retired records must have decision_status superseded")
    if proposed and decided and decided < proposed:
        errors.append(f"{location}: decided_at must not precede proposed_at")
    decision_time = decided or proposed
    if implemented and decision_time and implemented < decision_time:
        errors.append(f"{location}: implemented_at must not precede the decision time")

    expected_id = f"{record.object_key}/{record.topic}@{record_stamp(metadata['proposed_at'])}"
    if record.record_id != expected_id:
        errors.append(f"{location}: record_id must be {expected_id!r}")
    expected_heading = heading_for(record.record_id)
    if record.heading != expected_heading:
        errors.append(f"{location}: record heading must be {expected_heading!r}")

    affected = metadata.get("affected_objects")
    if not isinstance(affected, list):
        errors.append(f"{location}: affected_objects must be a list")
    else:
        affected_ids = [str(value) for value in affected]
        if len(affected_ids) != len(set(affected_ids)):
            errors.append(f"{location}: affected_objects must not contain duplicates")
        unknown = sorted(set(affected_ids) - registered_keys)
        if unknown:
            errors.append(f"{location}: unknown affected_objects: {', '.join(unknown)}")
        if record.object_key in affected_ids:
            errors.append(f"{location}: primary object must not be affected")

    supersedes = metadata.get("supersedes")
    if not isinstance(supersedes, list):
        errors.append(f"{location}: supersedes must be a list")
    else:
        predecessor_ids = [str(value) for value in supersedes]
        if len(predecessor_ids) > 1:
            errors.append(f"{location}: supersedes may contain at most one record")
        if len(predecessor_ids) != len(set(predecessor_ids)):
            errors.append(f"{location}: supersedes must not contain duplicates")
        for predecessor_id in predecessor_ids:
            predecessor = records_by_id.get(predecessor_id)
            if predecessor is None:
                errors.append(f"{location}: supersedes unknown record {predecessor_id!r}")
            elif predecessor.object_key != record.object_key or predecessor.topic != record.topic:
                errors.append(f"{location}: supersedes must stay in the same object and topic")
            elif predecessor.record_id == record.record_id:
                errors.append(f"{location}: a record cannot supersede itself")
            else:
                try:
                    predecessor_time = parse_timestamp(predecessor.metadata.get("proposed_at"))
                except (TypeError, ValueError):
                    predecessor_time = None
                if proposed and predecessor_time and proposed <= predecessor_time:
                    errors.append(
                        f"{location}: superseding record must be newer than {predecessor_id!r}"
                    )

    related = metadata.get("related_records", [])
    if not isinstance(related, list):
        errors.append(f"{location}: related_records must be a list when present")
    else:
        related_ids = [str(value) for value in related]
        if len(related_ids) != len(set(related_ids)):
            errors.append(f"{location}: related_records must not contain duplicates")
        for related_id in related_ids:
            if related_id == record.record_id:
                errors.append(f"{location}: a record cannot relate to itself")
            elif related_id not in records_by_id:
                errors.append(f"{location}: related_records contains unknown {related_id!r}")

    errors.extend(validate_canonical_doc(metadata.get("canonical_doc"), location))
    return errors


def validate_record_graph(records: list[Record], records_by_id: dict[str, Record]) -> list[str]:
    errors: list[str] = []
    predecessors: dict[str, list[str]] = {}
    successors: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        raw = record.metadata.get("supersedes")
        predecessor_ids = (
            [str(value) for value in raw if str(value) in records_by_id]
            if isinstance(raw, list)
            else []
        )
        predecessors[record.record_id] = predecessor_ids
        for predecessor_id in predecessor_ids:
            successors[predecessor_id].append(record)

    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(record_id: str) -> None:
        state[record_id] = 1
        stack.append(record_id)
        for predecessor_id in predecessors.get(record_id, []):
            if state.get(predecessor_id, 0) == 0:
                visit(predecessor_id)
            elif state.get(predecessor_id) == 1:
                start = stack.index(predecessor_id)
                errors.append("supersedes cycle: " + " -> ".join(stack[start:] + [predecessor_id]))
        stack.pop()
        state[record_id] = 2

    for record_id in records_by_id:
        if state.get(record_id, 0) == 0:
            visit(record_id)

    grouped: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for record in records:
        grouped[(record.object_key, record.topic)].append(record)
    for (object_key, topic), topic_records in grouped.items():
        for field, value, label in (
            ("decision_status", "proposed", "proposed"),
            ("decision_status", "accepted", "accepted"),
            ("implementation_status", "implemented", "implemented"),
        ):
            matches = [record for record in topic_records if record.metadata.get(field) == value]
            if len(matches) > 1:
                errors.append(f"{object_key}/{topic}: multiple {label} decision records")
        accepted = [
            record
            for record in topic_records
            if record.metadata.get("decision_status") == "accepted"
        ]
        proposed = [
            record
            for record in topic_records
            if record.metadata.get("decision_status") == "proposed"
        ]
        if (
            accepted
            and proposed
            and accepted[0].record_id not in predecessors.get(proposed[0].record_id, [])
        ):
            errors.append(
                f"{proposed[0].record_id}: proposed replacement must supersede "
                f"the accepted record {accepted[0].record_id!r}"
            )
        for record in topic_records:
            if record.metadata.get("decision_status") != "superseded":
                continue
            effective = [
                successor
                for successor in successors.get(record.record_id, [])
                if successor.metadata.get("decision_status") in {"accepted", "superseded"}
            ]
            if len(effective) != 1:
                errors.append(
                    f"{record.record_id}: superseded record must have one effective successor"
                )

    for predecessor_id, successor_records in successors.items():
        effective = [
            record
            for record in successor_records
            if record.metadata.get("decision_status") in {"accepted", "superseded"}
        ]
        if (
            effective
            and records_by_id[predecessor_id].metadata.get("decision_status") != "superseded"
        ):
            errors.append(f"{predecessor_id}: must be superseded when its successor is effective")
    return errors


def check() -> list[str]:
    try:
        registrations, archives = load_all()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [str(exc)]

    errors: list[str] = []
    keys = [registration.key for registration in registrations]
    paths = [registration.path for registration in registrations]
    if len(keys) != len(set(keys)):
        errors.append("docs/adr/README.md: duplicate object keys")
    if len(paths) != len(set(paths)):
        errors.append("docs/adr/README.md: duplicate object paths")

    expected_dirs: set[str] = set()
    for registration in registrations:
        expected_path = f"{registration.key}/README.md"
        if registration.path != expected_path:
            errors.append(f"docs/adr/README.md: {registration.key} path must be {expected_path}")
        expected_dirs.add(registration.key)
        archive = archives[registration.key]
        required = {"object_key", "title", "scope"}
        if set(archive.metadata) != required:
            errors.append(
                f"{archive.path.relative_to(ROOT)}: object front matter must contain "
                "only object_key, title, and scope"
            )
        if archive.metadata.get("object_key") != registration.key:
            errors.append(
                f"{archive.path.relative_to(ROOT)}: object_key must be {registration.key!r}"
            )
        if archive.metadata.get("scope") != registration.scope:
            errors.append(f"{archive.path.relative_to(ROOT)}: scope must match the registry")
        try:
            lines = markdown_lines(
                archive.text,
                source=str(archive.path.relative_to(ROOT)),
            )
        except ValueError as exc:
            errors.append(str(exc))
        else:
            h1_lines = [line for line in lines if line.outside_fence and H1_RE.fullmatch(line.text)]
            if len(h1_lines) != 1:
                errors.append(
                    f"{archive.path.relative_to(ROOT)}: object archive must contain exactly one H1"
                )
            else:
                h1 = H1_RE.fullmatch(h1_lines[0].text)
                if h1 is None:
                    raise AssertionError("validated H1 did not match")
                if h1.group("title") != archive.metadata.get("title"):
                    errors.append(
                        f"{archive.path.relative_to(ROOT)}: H1 must match the front matter title"
                    )
        unexpected_entries = sorted(
            child.name
            for child in archive.path.parent.iterdir()
            if child.name != "README.md" and (not child.is_file() or child.suffix != ".md")
        )
        if unexpected_entries:
            errors.append(
                f"{archive.path.parent.relative_to(ROOT)}: object directory may only contain "
                "README.md and topic Markdown files; found " + ", ".join(unexpected_entries)
            )
        for start, end in ((INDEX_START, INDEX_END), (RELATED_START, RELATED_END)):
            try:
                extract_between(archive.text, start, end)
            except ValueError as exc:
                errors.append(f"{archive.path.relative_to(ROOT)}: {exc}")

    actual_dirs = {path.name for path in ADR_DIR.iterdir() if path.is_dir()}
    if actual_dirs != expected_dirs:
        missing = sorted(expected_dirs - actual_dirs)
        extra = sorted(actual_dirs - expected_dirs)
        if missing:
            errors.append(f"docs/adr: missing object directories: {', '.join(missing)}")
        if extra:
            errors.append(f"docs/adr: unregistered object directories: {', '.join(extra)}")
    unexpected_root_entries = sorted(
        path.name for path in ADR_DIR.iterdir() if path.name != "README.md" and not path.is_dir()
    )
    if unexpected_root_entries:
        errors.append(
            "docs/adr: root may only contain README.md and registered object directories; found "
            + ", ".join(unexpected_root_entries)
        )

    records = [record for archive in archives.values() for record in archive.records]
    records_by_id: dict[str, Record] = {}
    for record in records:
        if record.record_id in records_by_id:
            errors.append(f"duplicate record_id: {record.record_id}")
        records_by_id[record.record_id] = record
    registered_keys = set(keys)
    for record in records:
        errors.extend(validate_record(record, registered_keys, records_by_id))
    errors.extend(validate_record_graph(records, records_by_id))

    try:
        rendered = rendered_readmes(registrations, archives)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        for path, expected in rendered.items():
            if path.read_text(encoding="utf-8") != expected:
                errors.append(
                    f"{path.relative_to(ROOT)}: generated indexes are stale; "
                    "run scripts/adr.py index"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("index", "check"))
    args = parser.parse_args()
    if args.command == "index":
        changed = generate_indexes(write=True)
        for path in changed:
            print(path.relative_to(ROOT))
        return 0
    errors = check()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("ADR check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

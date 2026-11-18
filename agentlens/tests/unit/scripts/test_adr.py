from __future__ import annotations

from typing import Any

import pytest
import yaml

from scripts import adr

README = """# ADR rules

<!-- adr-objects:start -->
| 对象键 | 对象档案 | 覆盖范围 |
|---|---|---|
| `trace` | [Trace](trace/README.md) | Trace decisions |
| `projection` | [Projection](projection/README.md) | Projection decisions |
<!-- adr-objects:end -->
"""

OBJECT_README = """---
object_key: {object_key}
title: {title}
scope: {scope}
---

# {title}

## 当前决策索引

<!-- adr-index:start -->
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
<!-- adr-related:end -->
"""


def _record(
    object_key: str,
    topic: str,
    stamp: str,
    proposed_at: str,
    *,
    decision_status: str = "accepted",
    implementation_status: str = "implemented",
    decided_at: str | None = "2026-08-23T10:05+08:00",
    implemented_at: str | None = "2026-08-23T10:10+08:00",
    supersedes: list[str] | None = None,
    related_records: list[str] | None = None,
    affected_objects: list[str] | None = None,
    canonical_doc: str = "docs/current.md#current-behavior",
    extra: dict[str, Any] | None = None,
    english: bool = False,
) -> str:
    record_id = f"{object_key}/{topic}@{stamp}"
    metadata: dict[str, Any] = {
        "record_id": record_id,
        "decision_status": decision_status,
        "implementation_status": implementation_status,
        "proposed_at": proposed_at,
        "decided_at": decided_at,
        "implemented_at": implemented_at,
        "supersedes": supersedes or [],
        "affected_objects": affected_objects or [],
        "canonical_doc": canonical_doc,
    }
    if related_records is not None:
        metadata["related_records"] = related_records
    if extra:
        metadata.update(extra)
    yaml_text = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).rstrip()
    record = (
        f"## {adr.heading_for(record_id)}\n\n"
        f"**{topic} decision**\n\n"
        f"```yaml\n{yaml_text}\n```\n\n"
        "### 背景\n\nDecision context.\n\n"
        "### 决策\n\nDecision.\n\n"
        "### 保持不变\n\nUnchanged.\n\n"
        "### 影响与风险\n\nRisk.\n\n"
        "### 验证\n\nVerification.\n\n"
        "### 实施说明\n\nImplementation.\n"
    )
    if english:
        for original, translated in (
            ("背景", "Background"),
            ("决策", "Decision"),
            ("保持不变", "Unchanged"),
            ("影响与风险", "Impact and risks"),
            ("验证", "Validation"),
            ("实施说明", "Implementation notes"),
        ):
            record = record.replace(f"### {original}\n", f"### {translated}\n")
    return record


def _topic(object_key: str, topic: str, *records: str) -> str:
    return (
        "---\n"
        f"object_key: {object_key}\n"
        f"topic: {topic}\n"
        f"title: {topic} decisions\n"
        "---\n\n"
        f"# {topic} decisions\n\n" + "\n".join(records)
    )


def _configure_repo(
    tmp_path,
    monkeypatch,
    *,
    trace_topics: dict[str, str] | None = None,
    projection_topics: dict[str, str] | None = None,
) -> None:
    adr_dir = tmp_path / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (tmp_path / "docs" / "current.md").write_text(
        '# Current document\n\n## Current behavior\n\n<a id="explicit-anchor"></a>\n'
    )
    (adr_dir / "README.md").write_text(README)
    for object_key, title, scope, topics in (
        ("trace", "Trace decisions", "Trace decisions", trace_topics or {}),
        (
            "projection",
            "Projection decisions",
            "Projection decisions",
            projection_topics or {},
        ),
    ):
        object_dir = adr_dir / object_key
        object_dir.mkdir()
        (object_dir / "README.md").write_text(
            OBJECT_README.format(object_key=object_key, title=title, scope=scope)
        )
        for filename, text in topics.items():
            (object_dir / filename).write_text(text)
    monkeypatch.setattr(adr, "ROOT", tmp_path)
    monkeypatch.setattr(adr, "ADR_DIR", adr_dir)
    monkeypatch.setattr(adr, "README", adr_dir / "README.md")


def _index_and_check() -> list[str]:
    adr.generate_indexes(write=True)
    return adr.check()


def test_check_accepts_directory_model_and_generates_consistent_links(tmp_path, monkeypatch):
    record_id = "trace/runtime-input@20260823-1000"
    record = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        affected_objects=["projection"],
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", record),
        },
    )

    assert record.startswith("## trace-runtime-input@20260823-1000\n")
    assert _index_and_check() == []
    trace_readme = (tmp_path / "docs" / "adr" / "trace" / "README.md").read_text()
    fragment = "trace-runtime-input20260823-1000"
    link = f"[{record_id}](runtime-input.md#{fragment})"
    assert trace_readme.count(link) == 2
    projection_readme = (tmp_path / "docs" / "adr" / "projection" / "README.md").read_text()
    assert f"[{record_id}](../trace/runtime-input.md#{fragment})" in projection_readme
    assert "`A → B` 表示 A 的决策影响 B" in projection_readme
    assert "| Trace → Projection |" in projection_readme


def test_check_accepts_empty_object_archive(tmp_path, monkeypatch):
    _configure_repo(tmp_path, monkeypatch)

    assert _index_and_check() == []
    trace_readme = (tmp_path / "docs" / "adr" / "trace" / "README.md").read_text()
    assert "当前没有已登记主题。" in trace_readme


@pytest.mark.parametrize(
    "english_registry", [False, True], ids=["legacy-registry", "english-registry"]
)
def test_check_accepts_english_record_sections(tmp_path, monkeypatch, english_registry):
    record = _record(
        "trace", "runtime-input", "20260823-1000", "2026-08-23T10:00+08:00", english=True
    ).replace("### Validation", "### Alternatives\n\nAnother option.\n\n### Validation")
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={"runtime-input.md": _topic("trace", "runtime-input", record)},
    )
    if english_registry:
        adr.README.write_text(
            README.replace(
                "| 对象键 | 对象档案 | 覆盖范围 |", "| Object key | Object archive | Scope |"
            )
        )

    assert _index_and_check() == []
    assert (
        "runtime-input.md#trace-runtime-input20260823-1000"
        in (adr.ADR_DIR / "trace" / "README.md").read_text()
    )


def test_english_registry_still_requires_matching_object_scope(tmp_path, monkeypatch):
    _configure_repo(tmp_path, monkeypatch)
    adr.README.write_text(
        README.replace(
            "| 对象键 | 对象档案 | 覆盖范围 |", "| Object key | Object archive | Scope |"
        )
    )
    readme = adr.ADR_DIR / "trace" / "README.md"
    readme.write_text(readme.read_text().replace("scope: Trace decisions", "scope: Other scope"))

    assert any("scope must match the registry" in error for error in _index_and_check())


def test_documented_record_template_passes_validation(tmp_path, monkeypatch):
    template = adr.README.read_text().split("````markdown\n", 1)[1].split("\n````", 1)[0]
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={"runtime-input.md": template},
    )
    canonical_doc = tmp_path / "docs" / "projection" / "trace-contract.md"
    canonical_doc.parent.mkdir()
    canonical_doc.write_text("# Current Trace contract\n")

    records = adr.parse_topic_file(adr.ADR_DIR / "trace" / "runtime-input.md", "trace")

    assert len(records) == 1
    assert records[0].record_id == "trace/runtime-input@20260823-1430"
    assert _index_and_check() == []


def test_check_rejects_legacy_hyphenated_record_heading(tmp_path, monkeypatch):
    record = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
    ).replace("runtime-input@20260823", "runtime-input-20260823", 1)
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={"runtime-input.md": _topic("trace", "runtime-input", record)},
    )

    assert any("<object>-<topic>@YYYYMMDD-HHMM" in error for error in adr.check())


def test_check_rejects_forbidden_repeated_ownership_fields(tmp_path, monkeypatch):
    record = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        extra={"topic": "runtime-input", "primary_object": "trace"},
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", record),
        },
    )

    assert any("forbidden fields" in error for error in _index_and_check())


def test_check_rejects_topic_filename_mismatch(tmp_path, monkeypatch):
    record = _record("trace", "runtime-input", "20260823-1000", "2026-08-23T10:00+08:00")
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "other-name.md": _topic("trace", "runtime-input", record),
        },
    )

    assert any("filename must be runtime-input.md" in error for error in adr.check())


def test_check_rejects_topic_h1_mismatch(tmp_path, monkeypatch):
    record = _record("trace", "runtime-input", "20260823-1000", "2026-08-23T10:00+08:00")
    topic = _topic("trace", "runtime-input", record).replace(
        "# runtime-input decisions",
        "# Wrong title",
        1,
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={"runtime-input.md": topic},
    )

    assert any("H1 must match the front matter title" in error for error in adr.check())


@pytest.mark.parametrize("english", [False, True], ids=["legacy", "english"])
@pytest.mark.parametrize("defect", ["missing", "out-of-order", "duplicate", "translated-duplicate"])
def test_check_rejects_malformed_required_sections(tmp_path, monkeypatch, english, defect):
    record = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        english=english,
    )
    heading = "Validation" if english else "验证"
    section = f"### {heading}\n\nVerification.\n\n"
    if defect == "missing":
        record = record.replace(section, "", 1)
    elif defect == "out-of-order":
        record = record.replace(section, "", 1) + "\n" + section
    else:
        duplicate_heading = (
            ("验证" if english else "Validation") if defect == "translated-duplicate" else heading
        )
        record += f"\n### {duplicate_heading}\n\nDuplicate validation.\n"
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={"runtime-input.md": _topic("trace", "runtime-input", record)},
    )

    assert any("required sections in template order" in error for error in adr.check())


def test_check_rejects_object_h1_mismatch(tmp_path, monkeypatch):
    _configure_repo(tmp_path, monkeypatch)
    readme = tmp_path / "docs" / "adr" / "trace" / "README.md"
    readme.write_text(readme.read_text().replace("# Trace decisions", "# Wrong title", 1))

    assert any("H1 must match the front matter title" in error for error in adr.check())


def test_check_rejects_impossible_timestamp(tmp_path, monkeypatch):
    record = _record(
        "trace",
        "runtime-input",
        "20269999-2599",
        "2026-99-99T25:99+08:00",
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", record),
        },
    )

    assert any("proposed_at must be a real" in error for error in _index_and_check())


def test_check_rejects_implemented_at_before_decision(tmp_path, monkeypatch):
    record = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        decided_at="2026-08-23T11:00+08:00",
        implemented_at="2026-08-23T10:30+08:00",
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", record),
        },
    )

    assert any("implemented_at must not precede" in error for error in _index_and_check())


def test_check_rejects_missing_canonical_doc_anchor(tmp_path, monkeypatch):
    record = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        canonical_doc="docs/current.md#missing",
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", record),
        },
    )

    assert any("canonical_doc anchor does not exist" in error for error in _index_and_check())


def test_check_rejects_cross_topic_supersedes(tmp_path, monkeypatch):
    older_id = "trace/other@20260823-0950"
    newer = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        supersedes=[older_id],
    )
    older = _record("trace", "other", "20260823-0950", "2026-08-23T09:50+08:00")
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", newer),
            "other.md": _topic("trace", "other", older),
        },
    )

    assert any(
        "supersedes must stay in the same object and topic" in error for error in _index_and_check()
    )


def test_check_rejects_supersession_cycle(tmp_path, monkeypatch):
    older_id = "trace/runtime-input@20260823-1000"
    newer_id = "trace/runtime-input@20260823-1001"
    older = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        decision_status="superseded",
        implementation_status="retired",
        supersedes=[newer_id],
    )
    newer = _record(
        "trace",
        "runtime-input",
        "20260823-1001",
        "2026-08-23T10:01+08:00",
        decision_status="superseded",
        implementation_status="retired",
        supersedes=[older_id],
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", newer, older),
        },
    )

    assert any("supersedes cycle" in error for error in _index_and_check())


def test_generated_related_records_show_no_direction(tmp_path, monkeypatch):
    projection_id = "projection/runtime-facts@20260823-1001"
    trace_record = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        related_records=[projection_id],
    )
    projection_record = _record(
        "projection",
        "runtime-facts",
        "20260823-1001",
        "2026-08-23T10:01+08:00",
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={"runtime-input.md": _topic("trace", "runtime-input", trace_record)},
        projection_topics={
            "runtime-facts.md": _topic("projection", "runtime-facts", projection_record)
        },
    )

    assert _index_and_check() == []
    trace_readme = (tmp_path / "docs" / "adr" / "trace" / "README.md").read_text()
    projection_readme = (tmp_path / "docs" / "adr" / "projection" / "README.md").read_text()
    assert "| Projection ↔ Trace |" in trace_readme
    assert "| Trace ↔ Projection |" in projection_readme


def test_check_rejects_unknown_related_record(tmp_path, monkeypatch):
    record = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        related_records=["projection/missing@20260823-1000"],
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", record),
        },
    )

    assert any("related_records contains unknown" in error for error in _index_and_check())


def test_proposed_replacement_must_supersede_current_accepted(tmp_path, monkeypatch):
    accepted = _record("trace", "runtime-input", "20260823-1000", "2026-08-23T10:00+08:00")
    proposal = _record(
        "trace",
        "runtime-input",
        "20260823-1001",
        "2026-08-23T10:01+08:00",
        decision_status="proposed",
        implementation_status="not_started",
        decided_at=None,
        implemented_at=None,
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", proposal, accepted),
        },
    )

    assert any("proposed replacement must supersede" in error for error in _index_and_check())


def test_proposed_record_cannot_be_implemented(tmp_path, monkeypatch):
    proposal = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        decision_status="proposed",
        decided_at=None,
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", proposal),
        },
    )

    assert any(
        "proposed records must have implementation_status not_started" in error
        for error in _index_and_check()
    )


def test_accepted_record_cannot_be_retired(tmp_path, monkeypatch):
    record = _record(
        "trace",
        "runtime-input",
        "20260823-1000",
        "2026-08-23T10:00+08:00",
        implementation_status="retired",
    )
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={"runtime-input.md": _topic("trace", "runtime-input", record)},
    )

    assert any(
        "retired records must have decision_status superseded" in error
        for error in _index_and_check()
    )


def test_check_rejects_multiple_current_records(tmp_path, monkeypatch):
    first = _record("trace", "runtime-input", "20260823-1000", "2026-08-23T10:00+08:00")
    second = _record("trace", "runtime-input", "20260823-1001", "2026-08-23T10:01+08:00")
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", second, first),
        },
    )

    errors = _index_and_check()
    assert any("multiple accepted" in error for error in errors)
    assert any("multiple implemented" in error for error in errors)


def test_check_reports_stale_generated_indexes(tmp_path, monkeypatch):
    record = _record("trace", "runtime-input", "20260823-1000", "2026-08-23T10:00+08:00")
    _configure_repo(
        tmp_path,
        monkeypatch,
        trace_topics={
            "runtime-input.md": _topic("trace", "runtime-input", record),
        },
    )

    assert any("generated indexes are stale" in error for error in adr.check())


def test_check_rejects_root_topic_file(tmp_path, monkeypatch):
    _configure_repo(tmp_path, monkeypatch)
    (tmp_path / "docs" / "adr" / "stray.md").write_text("# stray\n")

    assert any("root may only contain README.md" in error for error in adr.check())


def test_check_rejects_nested_object_directory(tmp_path, monkeypatch):
    _configure_repo(tmp_path, monkeypatch)
    (tmp_path / "docs" / "adr" / "trace" / "nested").mkdir()

    assert any("object directory may only contain" in error for error in adr.check())

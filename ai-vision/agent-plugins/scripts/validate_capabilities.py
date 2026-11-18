#!/usr/bin/env python3
"""Validate the repository capability evidence and cross-agent status matrix."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = (
    "claudecode",
    "codex",
    "kimi",
    "openclaw",
    "opencode",
    "pi",
    "hermes",
)
CAPABILITY_IDS = (
    "A1",
    *(f"B{index}" for index in range(1, 7)),
    *(f"C{index}" for index in range(1, 8)),
    *(f"D{index}" for index in range(1, 5)),
    "E1",
    "E2",
    "E3",
)
SCORES = "✅🟡❌⚪"

SCORE_RE = re.compile(
    rf"^\s*-\s*\*\*(?:评分|Status|Score)\*\*\s*[:：]\s*([{SCORES}])\s*$",
    re.MULTILINE,
)
CITATION_RE = re.compile(r"`([^`\n]+?):(\d+)(?:-(\d+))?`")
MATRIX_RE = re.compile(r"^\| \[([A-E]\d+) [^\]]+\]\([^)]*\)")

CODE_HEADINGS = (
    "**代码证据**:",
    "**代码证据**：",
    "**代码引证**:",
    "**代码引证**：",
    "**Code evidence**:",
    "**Code citations**:",
)
REPLAY_HEADINGS = (
    "**Replay/source 证据**:",
    "**Replay/source 证据**：",
    "**Replay 例子**:",
    "**Replay 例子**：",
    "**Replay evidence**:",
    "**Replay/source evidence**:",
    "**Runtime evidence**:",
    "**测试证据**:",
    "**回归证据**:",
)
SOURCE_ALIASES = {
    "cchooks": "claudecode",
    "cdxhooks": "codex",
    "kimihooks": "kimi",
    "observ-openclaw": "openclaw",
    "observ-opencode": "opencode",
}


def _source_candidates(document: Path, raw: str) -> list[Path]:
    path = Path(raw)
    candidates = [ROOT / path, ROOT / document.parts[0] / path]

    if path.parts and path.parts[0] in SOURCE_ALIASES:
        candidates.append(ROOT / SOURCE_ALIASES[path.parts[0]] / path)

    # observ_hermes is a compatibility facade. The canonical implementation is
    # owned by the separate hermes-core package.
    if raw.startswith("hermes/observ_hermes/"):
        candidates.append(
            ROOT
            / raw.replace(
                "hermes/observ_hermes/",
                "hermes/hermes-core/aivision_hermes_core/",
            )
        )
    return candidates


def _resolve_source(document: Path, raw: str) -> Path | None:
    return next((candidate for candidate in _source_candidates(document, raw) if candidate.is_file()), None)


def _validate_citation(
    document: Path,
    raw: str,
    start: int,
    end: int,
    errors: list[str],
) -> bool:
    # Runtime artifact paths and URLs are evidence references, not source-code
    # citations. They are deliberately not checked as repository files.
    if raw.startswith(("~/", "http://", "https://")):
        return False

    source = _resolve_source(document, raw)
    if source is None:
        errors.append(f"{document}: citation source does not exist: {raw}")
        return False

    line_count = len(source.read_text(encoding="utf-8", errors="replace").splitlines())
    if not 1 <= start <= end <= line_count:
        errors.append(
            f"{document}: citation range {raw}:{start}-{end} exceeds "
            f"{source} ({line_count} lines)"
        )
    return True


def _validate_evidence(agent: str, capability_id: str, errors: list[str]) -> str | None:
    path = ROOT / agent / "capabilities" / f"{capability_id}.md"
    if not path.is_file():
        errors.append(f"missing evidence file: {path}")
        return None

    text = path.read_text(encoding="utf-8")
    headings = re.findall(rf"^#\s+{re.escape(capability_id)}\b.*$", text, re.MULTILINE)
    if len(headings) != 1:
        errors.append(f"{path}: expected one #{capability_id} heading, found {len(headings)}")

    score_matches = SCORE_RE.findall(text)
    if len(score_matches) != 1:
        errors.append(f"{path}: expected one score field, found {len(score_matches)}")
        score = None
    else:
        score = score_matches[0]

    if not any(heading in text for heading in CODE_HEADINGS):
        errors.append(f"{path}: missing code evidence heading")
    if not any(heading in text for heading in REPLAY_HEADINGS):
        errors.append(f"{path}: missing replay/runtime/source evidence heading")
    if capability_id == "E3":
        specification = "../../docs/capabilities/E-meta-trigger.md"
        if f"]({specification})" not in text or not (path.parent / specification).is_file():
            errors.append(f"{path}: E3 evidence must link to the external trigger specification")

    citation_count = 0
    for match in CITATION_RE.finditer(text):
        raw = match.group(1)
        start = int(match.group(2))
        end = int(match.group(3) or match.group(2))
        if _validate_citation(path, raw, start, end, errors):
            citation_count += 1
    if citation_count == 0:
        errors.append(f"{path}: no repository code file:line citation")

    return score


def _aggregate_score(agent: str, capability_id: str, text: str, errors: list[str]) -> str | None:
    rows: list[str] = []
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3 or not re.search(rf"\b{re.escape(capability_id)}\b", cells[0]):
            continue
        # Pi's English aggregate table has the score in column 2. Other
        # aggregate tables use the same layout; avoid counting the header.
        if len(cells) >= 2 and cells[1] in SCORES:
            rows.append(line)

    if len(rows) != 1:
        errors.append(
            f"{ROOT / agent / 'CAPABILITIES.md'}: expected one aggregate row for "
            f"{capability_id}, found {len(rows)}"
        )
        return None

    cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
    score = next((cell for cell in cells[1:3] if cell in SCORES), None)
    if score is None:
        errors.append(f"{ROOT / agent / 'CAPABILITIES.md'}: aggregate row {capability_id} has no score")
    return score


def _validate_agent(agent: str, errors: list[str]) -> dict[str, str]:
    evidence_dir = ROOT / agent / "capabilities"
    actual_ids = {path.stem for path in evidence_dir.glob("[A-E][0-9].md")}
    expected_ids = set(CAPABILITY_IDS)
    for missing in sorted(expected_ids - actual_ids):
        errors.append(f"{agent}: missing capability evidence {missing}.md")
    for extra in sorted(actual_ids - expected_ids):
        errors.append(f"{agent}: unexpected capability evidence {extra}.md")

    aggregate_path = ROOT / agent / "CAPABILITIES.md"
    if not aggregate_path.is_file():
        errors.append(f"missing aggregate report: {aggregate_path}")
        return {}
    aggregate_text = aggregate_path.read_text(encoding="utf-8")

    scores: dict[str, str] = {}
    for capability_id in CAPABILITY_IDS:
        evidence_score = _validate_evidence(agent, capability_id, errors)
        aggregate_score = _aggregate_score(agent, capability_id, aggregate_text, errors)
        if evidence_score and aggregate_score and evidence_score != aggregate_score:
            errors.append(
                f"{agent} {capability_id}: evidence score {evidence_score} != "
                f"aggregate score {aggregate_score}"
            )
        if evidence_score:
            scores[capability_id] = evidence_score

    summary = re.search(
        rf"^\s*✅\s*(\d+)\s*/\s*🟡\s*(\d+)\s*/\s*❌\s*(\d+)\s*/\s*⚪\s*(\d+)\b",
        aggregate_text,
        re.MULTILINE,
    )
    if summary is None:
        # Pi uses an English aggregate table rather than the shared one-line
        # summary. The evidence files and table still provide the same check.
        summary_counts: Counter[str] = Counter()
        for line in aggregate_text.splitlines():
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[1] in SCORES:
                summary_counts[cells[1]] += 1
        if not summary_counts:
            errors.append(f"{aggregate_path}: missing score summary")
        elif any(summary_counts[score] != Counter(scores.values())[score] for score in SCORES):
            errors.append(
                f"{aggregate_path}: table counts {dict(summary_counts)} != "
                f"evidence counts {dict(Counter(scores.values()))}"
            )
    else:
        expected = Counter(scores.values())
        actual = tuple(int(value) for value in summary.groups())
        wanted = tuple(expected[score] for score in SCORES)
        if actual != wanted:
            errors.append(f"{aggregate_path}: summary {actual} != evidence counts {wanted}")

    return scores


def _validate_matrix(scores: dict[str, dict[str, str]], errors: list[str]) -> None:
    path = ROOT / "CAPABILITIES.md"
    text = path.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if MATRIX_RE.match(line)]
    # E4 is registered as a standard this round but not evaluated (all "—"),
    # so the matrix may carry it as an unevaluated trailing placeholder row.
    row_ids = [MATRIX_RE.match(row).group(1) for row in rows]  # type: ignore[union-attr]
    if row_ids not in (list(CAPABILITY_IDS), [*CAPABILITY_IDS, "E4"]):
        errors.append(f"{path}: matrix capability rows are not the expected ordered 22 IDs")
        return

    expected_columns = len(AGENTS) + 2
    for line in rows:
        match = MATRIX_RE.match(line)
        assert match is not None
        if match.group(1) == "E4":
            continue
        assert match is not None
        capability_id = match.group(1)
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != expected_columns:
            errors.append(
                f"{path}: {capability_id} matrix row has {len(cells)} cells, "
                f"expected {expected_columns}"
            )
            continue
        for index, agent in enumerate(AGENTS, start=1):
            status = re.fullmatch(rf"\[([{SCORES}])\]\(([^)]+)\)", cells[index])
            if status is None:
                errors.append(f"{path}: {capability_id}/{agent} is not a linked four-state cell")
                continue
            score, target = status.groups()
            target_path = ROOT / target
            expected_target = Path(agent) / "capabilities" / f"{capability_id}.md"
            if target_path != ROOT / expected_target:
                errors.append(
                    f"{path}: {capability_id}/{agent} links to {target}, "
                    f"expected {expected_target.as_posix()}"
                )
            if not target_path.is_file():
                errors.append(f"{path}: missing matrix target {target}")
            expected_score = scores.get(agent, {}).get(capability_id)
            if expected_score and score != expected_score:
                errors.append(
                    f"{path}: {capability_id}/{agent} matrix score {score} != "
                    f"evidence score {expected_score}"
                )


def validate() -> tuple[dict[str, dict[str, str]], list[str]]:
    errors: list[str] = []
    scores = {agent: _validate_agent(agent, errors) for agent in AGENTS}
    _validate_matrix(scores, errors)
    return scores, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true", help="only print failures")
    args = parser.parse_args(argv)

    scores, errors = validate()
    if errors:
        print(f"capability validation failed: {len(errors)} issue(s)", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(
            f"capability validation passed: {len(scores)} agents × "
            f"{len(CAPABILITY_IDS)} capabilities; matrix links={len(AGENTS) * len(CAPABILITY_IDS)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import re
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "hermes" / "CAPABILITIES.md"

EXPECTED_SCORES = {
    "A1": "✅",
    "B1": "✅",
    "B2": "✅",
    "B3": "✅",
    "B4": "✅",
    "B5": "🟡",
    "B6": "🟡",
    "C1": "✅",
    "C2": "🟡",
    "C3": "✅",
    "C4": "✅",
    "C5": "✅",
    "C6": "⚪",
    "C7": "✅",
    "D1": "⚪",
    "D2": "✅",
    "D3": "⚪",
    "D4": "🟡",
    "E1": "⚪",
    "E2": "⚪",
    "E3": "❌",
}
EXPECTED_COUNTS = {"✅": 11, "🟡": 4, "❌": 1, "⚪": 5}


def _sections(markdown: str) -> dict[str, str]:
    matches = list(re.finditer(r"^### ([ABCDE]\d+) .*$", markdown, re.MULTILINE))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections[match.group(1)] = markdown[match.start():end]
    return sections


class CapabilitiesDocumentTests(unittest.TestCase):
    def test_exact_ids_scores_and_evidence(self) -> None:
        markdown = DOC.read_text(encoding="utf-8")
        sections = _sections(markdown)
        self.assertEqual(set(sections), set(EXPECTED_SCORES))

        scores: dict[str, str] = {}
        for capability_id, section in sections.items():
            score_match = re.search(r"- \*\*评分\*\*: ([✅🟡❌⚪])", section)
            self.assertIsNotNone(score_match, f"{capability_id} missing score field")
            assert score_match is not None
            scores[capability_id] = score_match.group(1)
            self.assertIn("**代码引证**:", section, f"{capability_id} missing code evidence heading")
            self.assertIn("**Runtime evidence**:", section, f"{capability_id} missing runtime evidence heading")
            self.assertRegex(section, r"`hermes/[^`]+:\d+(?:-\d+)?`", f"{capability_id} missing code file:line")
            self.assertIn(
                "~/.aivision/hermes/golden/",
                section,
                f"{capability_id} missing golden runtime pointer",
            )

        self.assertEqual(scores, EXPECTED_SCORES)
        counts = Counter(scores.values())
        self.assertEqual(
            {status: counts[status] for status in EXPECTED_COUNTS},
            EXPECTED_COUNTS,
        )

        summary_match = re.search(
            r"✅\s+(\d+) / 🟡\s+(\d+) / ❌\s+(\d+) / ⚪\s+(\d+)",
            markdown,
        )
        self.assertIsNotNone(summary_match)
        assert summary_match is not None
        self.assertEqual(
            tuple(map(int, summary_match.groups())),
            (
                EXPECTED_COUNTS["✅"],
                EXPECTED_COUNTS["🟡"],
                EXPECTED_COUNTS["❌"],
                EXPECTED_COUNTS["⚪"],
            ),
        )


if __name__ == "__main__":
    unittest.main()

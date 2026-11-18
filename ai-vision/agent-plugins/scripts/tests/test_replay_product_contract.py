#!/usr/bin/env python3
"""Lock the repository guidance contract for production/replay separation."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
METHODOLOGY = (ROOT / "METHODOLOGY.md").read_text(encoding="utf-8")


class ReplayProductContractTests(unittest.TestCase):
    def test_guidance_defines_both_product_families(self) -> None:
        self.assertIn("| External binary |", AGENTS)
        self.assertIn("| In-process plugin |", AGENTS)
        self.assertIn("binary package built, released, and installed separately", AGENTS)
        self.assertIn("外部二进制", METHODOLOGY)
        self.assertIn("进程内插件", METHODOLOGY)
        self.assertIn("独立 replay", METHODOLOGY)

    def test_guidance_requires_explicit_replay_enablement(self) -> None:
        for name in (
            "OPENCLAW_REPLAY_CAPTURE_ENABLED",
            "OPENCODE_REPLAY_CAPTURE_ENABLED",
            "PIREPLAY_CAPTURE_ENABLED",
        ):
            self.assertIn(name, AGENTS)
        self.assertIn("captureEnabled", METHODOLOGY)
        self.assertIn("不创建目录、不写文件", METHODOLOGY)

    def test_guidance_locks_raw_capture_and_offline_normalization(self) -> None:
        for document in (AGENTS, METHODOLOGY):
            self.assertIn("raw callback", document)
            self.assertIn("offline", document)
            self.assertIn("semantic engine", document)

    def test_guidance_locks_local_only_replay_network(self) -> None:
        for document in (AGENTS, METHODOLOGY):
            self.assertIn("http://127.0.0.1:4319", document)
            self.assertIn("credential", document)

    def test_guidance_rejects_the_old_universal_contract(self) -> None:
        combined = f"{AGENTS}\n{METHODOLOGY}"
        self.assertNotIn("Transcript 是唯一事实来源", combined)
        self.assertNotIn("实时 hook 与 replay CLI 必须吃同一份", combined)
        self.assertNotIn("hooks 支持 {PREFIX}_SESSION_DIR / _REPLAY_DIR / _DEBUG", combined)
        self.assertNotIn("环境变量四件套生效", combined)
        self.assertNotIn("未设置时写入对应的 `~/.aivision", combined)

    def test_guidance_freezes_production_collection(self) -> None:
        self.assertIn("Existing OpenCode/OpenClaw/Pi production hook-to-OTel behavior", AGENTS)
        self.assertIn("must not load a replay plugin", AGENTS)
        self.assertIn("生产开关不启用 replay", METHODOLOGY)

    def test_binary_contract_disallows_transcript_copy(self) -> None:
        self.assertIn("Production binary hooks do not copy host transcripts", AGENTS)
        self.assertIn("replay is a separately released and installed binary package", AGENTS)
        self.assertIn("生产 hook 不复制", METHODOLOGY)
        self.assertIn("独立 replay", METHODOLOGY)


if __name__ == "__main__":
    unittest.main()

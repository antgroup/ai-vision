from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
COMPONENTS = {
    "claudecode": ("cc", "TS=probe.jsonl"),
    "codex": ("cdx", "TS=probe.jsonl"),
    "openclaw": ("ocw", "TS=probe.jsonl"),
    "opencode": ("ocd", "TS=probe.jsonl"),
    "pi": ("pi", "TS=probe.jsonl"),
    "kimi": ("kimi", "HL=probe.ndjson"),
    "hermes": ("hermes", "TS=probe.jsonl"),
}


def run_make(component: str, *args: str) -> str:
    proc = subprocess.run(
        ["make", "-n", "-C", str(REPO / component), *args],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"make failed ({component} {' '.join(args)}):\n{proc.stdout}")
    return proc.stdout


class MakeArtifactPathTests(unittest.TestCase):
    def test_doctor_reads_canonical_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "aivision"
            (root / "cc" / "transcripts").mkdir(parents=True)
            (root / "cc" / "replays").mkdir(parents=True)
            env = os.environ.copy()
            env["AIVISION_HOME"] = str(root)
            proc = subprocess.run(
                ["bash", str(REPO / "scripts" / "doctor.sh"), "claudecode"],
                cwd=REPO,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(str(root / "cc" / "transcripts"), proc.stdout)
            self.assertIn(str(root / "cc" / "replays"), proc.stdout)

    def test_replay_aliases_use_canonical_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "aivision"
            for component, (short, alias) in COMPONENTS.items():
                with self.subTest(component=component):
                    output = run_make(component, "replay", alias, f"AIVISION_HOME={root}")
                    self.assertIn(str(root / short / "transcripts"), output)
                    self.assertIn(str(root / short / "replays"), output)

    def test_explicit_absolute_transcript_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "aivision"
            explicit = Path(tmp) / "outside" / "input.jsonl"
            for component, (short, _alias) in COMPONENTS.items():
                variable = "HOOK_LOG" if component == "kimi" else "TRANSCRIPT"
                with self.subTest(component=component):
                    output = run_make(
                        component,
                        "replay",
                        f"{variable}={explicit}",
                        f"AIVISION_HOME={root}",
                    )
                    self.assertIn(str(explicit), output)
                    self.assertIn(str(root / short / "replays"), output)

    def test_hermes_runtime_directory_overrides_remain_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcripts = Path(tmp) / "runtime-transcripts"
            replays = Path(tmp) / "runtime-replays"
            output = run_make(
                "hermes",
                "replay",
                "TS=probe.jsonl",
                f"OBSERV_HERMES_SESSION_DIR={transcripts}",
                f"OBSERV_HERMES_REPLAY_DIR={replays}",
            )
            self.assertIn(str(transcripts / "probe.jsonl"), output)
            self.assertIn(str(replays), output)

    def test_empty_make_overrides_fall_back_to_canonical_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected_root = Path(tmp) / ".aivision"
            for component, (short, alias) in COMPONENTS.items():
                with self.subTest(component=component):
                    output = run_make(
                        component,
                        "replay",
                        alias,
                        f"HOME={tmp}",
                        "AIVISION_HOME=",
                        "ARTIFACT_DIR=",
                        "TRANSCRIPT_DIR=",
                        "REPLAY_DIR=",
                        "GOLDEN_DIR=",
                    )
                    self.assertIn(str(expected_root / short / "transcripts"), output)
                    self.assertIn(str(expected_root / short / "replays"), output)

    def test_clean_only_removes_known_replay_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "aivision"
            for component, (short, _alias) in COMPONENTS.items():
                with self.subTest(component=component):
                    output = run_make(component, "clean", f"AIVISION_HOME={root}")
                    replay_dir = str(root / short / "replays")
                    self.assertIn(replay_dir, output)
                    self.assertNotIn("npm run clean", output)
                    self.assertNotIn("cargo clean", output)
                    self.assertNotIn("rm -rf", output)
                    artifact_rm_lines = [
                        line
                        for line in output.splitlines()
                        if re.match(r"^rm\s", line)
                    ]
                    self.assertTrue(artifact_rm_lines, output)
                    for line in artifact_rm_lines:
                        self.assertIn(replay_dir, line)
                        self.assertNotIn(str(root / short / "transcripts"), line)
                        self.assertNotIn(str(root / short / "golden"), line)

    def test_root_report_and_golden_targets_honor_explicit_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            replay = Path(tmp) / "custom-replays"
            golden = Path(tmp) / "custom-golden"
            transcript = Path(tmp) / "empty-transcripts"
            replay.mkdir()
            transcript.mkdir()
            (replay / "spans_probe.jsonl").write_text("{}\n")
            report = subprocess.run(
                [
                    "make",
                    "report-check",
                    "cc",
                    f"REPLAY_DIR={replay}",
                    f"TRANSCRIPT_DIR={transcript}",
                    "REPORT_CHECK_DRY_RUN=1",
                ],
                cwd=REPO,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(report.returncode, 0, report.stdout)
            self.assertIn(str(replay), report.stdout)

            golden.mkdir()
            (golden / "spans_probe.jsonl").write_text("{}\n")
            golden_report = subprocess.run(
                [
                    "make",
                    "report-check-golden",
                    "cc",
                    f"GOLDEN_DIR={golden}",
                    "REPORT_CHECK_DRY_RUN=1",
                ],
                cwd=REPO,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(golden_report.returncode, 0, golden_report.stdout)
            self.assertIn(str(golden), golden_report.stdout)

    def test_golden_collect_passes_absolute_canonical_paths_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            golden = Path(tmp) / "golden"
            proc = subprocess.run(
                ["make", "-n", "golden-collect", "cc", f"GOLDEN_DIR={golden}"],
                cwd=REPO,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn(f'golden_dir="{golden}"', proc.stdout)
            self.assertIn('CCHOOKS_REPLAY_DIR="$replay_dir"', proc.stdout)
            self.assertIn('CCHOOKS_SESSION_DIR="$transcript_dir"', proc.stdout)
            self.assertNotIn(f'$PWD/{golden}', proc.stdout)

    def test_withdrawn_agent_shorthands_are_unknown(self) -> None:
        for shorthand in ("cf", "csr", "cursor"):
            with self.subTest(shorthand=shorthand):
                proc = subprocess.run(
                    ["make", "-n", "help", shorthand],
                    cwd=REPO,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertIn(f"Unknown agent '{shorthand}'", proc.stdout)

    def test_golden_override_remains_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "custom-golden"
            for component in COMPONENTS:
                with self.subTest(component=component):
                    proc = subprocess.run(
                        [
                            "make",
                            "-pn",
                            "-C",
                            str(REPO / component),
                            f"GOLDEN_DIR={override}",
                            "help",
                        ],
                        cwd=REPO,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                    self.assertRegex(
                        proc.stdout,
                        rf"(?m)^GOLDEN_DIR\s*[:?+]?=\s*{re.escape(str(override))}$",
                    )


if __name__ == "__main__":
    unittest.main()

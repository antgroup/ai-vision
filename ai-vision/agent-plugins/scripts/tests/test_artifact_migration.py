from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from aivision_artifacts import ARTIFACT_KINDS, COMPONENTS, artifact_path


def load_migration_module():
    loader = SourceFileLoader("migrate_artifacts", str(SCRIPTS / "migrate-artifacts"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load migrate-artifacts")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


migration = load_migration_module()


class ArtifactPathTests(unittest.TestCase):
    def test_all_default_paths_use_fake_home(self):
        with tempfile.TemporaryDirectory() as value:
            env = {"HOME": value}
            for component in COMPONENTS:
                for kind in ARTIFACT_KINDS:
                    self.assertEqual(
                        artifact_path(component, kind, environ=env),
                        Path(value) / ".aivision" / component / kind,
                    )

    def test_aivision_home_and_aliases(self):
        env = {"HOME": "/ignored", "AIVISION_HOME": "/tmp/shared-aivision"}
        self.assertEqual(
            artifact_path("km", "golden", environ=env),
            Path("/tmp/shared-aivision/kimi/golden"),
        )
        self.assertEqual(
            artifact_path("openclaw", "transcripts", environ=env),
            Path("/tmp/shared-aivision/ocw/transcripts"),
        )
        self.assertEqual(
            artifact_path("hm", "replays", environ=env),
            Path("/tmp/shared-aivision/hermes/replays"),
        )

    def test_empty_aivision_home_falls_back(self):
        env = {"HOME": "/tmp/fake-home", "AIVISION_HOME": ""}
        self.assertEqual(
            artifact_path("cc", "replays", environ=env),
            Path("/tmp/fake-home/.aivision/cc/replays"),
        )


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.worktree_one = self.root / "worktree-one"
        self.worktree_two = self.root / "worktree-two"
        self.aivision_home = self.root / "home" / ".aivision"
        self.worktree_one.mkdir()
        self.worktree_two.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, worktree: Path, relative: str, content: str) -> Path:
        path = worktree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def run_cli(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "migrate-artifacts"),
                "--source-worktree",
                str(self.worktree_one),
                "--source-worktree",
                str(self.worktree_two),
                "--aivision-home",
                str(self.aivision_home),
                *extra,
            ],
            capture_output=True,
            text=True,
        )

    def test_dry_run_does_not_write(self):
        source = self.write(self.worktree_one, "claudecode/transcripts/session_a.jsonl", "one\n")
        result = self.run_cli("--component", "cc", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            [Path(value).resolve() for value in payload["actions"][0]["sources"]],
            [source.resolve()],
        )
        self.assertFalse(self.aivision_home.exists())

    def test_apply_preserves_sources_and_is_idempotent(self):
        first = self.write(self.worktree_one, "claudecode/transcripts/session_a.jsonl", "same\n")
        second = self.write(self.worktree_two, "claudecode/transcripts/session_a.jsonl", "same\n")
        result = self.run_cli("--component", "claudecode", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        destination = self.aivision_home / "cc/transcripts/session_a.jsonl"
        self.assertEqual(destination.read_text(encoding="utf-8"), "same\n")
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(len(list((self.aivision_home / ".migration").glob("*.manifest.json"))), 1)
        for component in COMPONENTS:
            for kind in ARTIFACT_KINDS:
                self.assertTrue((self.aivision_home / component / kind).is_dir())

        rerun = self.run_cli("--component", "cc", "--apply")
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        journals = list((self.aivision_home / ".migration").glob("*.jsonl"))
        all_states = [
            json.loads(line)["state"]
            for journal in journals
            for line in journal.read_text().splitlines()
        ]
        self.assertIn("identical", all_states)

    def test_collision_aborts_before_destination_writes(self):
        self.write(self.worktree_one, "codex/replays/spans_a.jsonl", "one\n")
        self.write(self.worktree_two, "codex/replays/spans_a.jsonl", "two\n")
        result = self.run_cli("--component", "cdx", "--apply")
        self.assertEqual(result.returncode, 3)
        self.assertIn("collision:", result.stderr)
        self.assertFalse(self.aivision_home.exists())

    def test_existing_different_destination_is_collision(self):
        self.write(self.worktree_one, "pi/golden/replays/spans_a.jsonl", "source\n")
        destination = self.aivision_home / "pi/golden/replays/spans_a.jsonl"
        destination.parent.mkdir(parents=True)
        destination.write_text("destination\n", encoding="utf-8")
        before = destination.read_bytes()
        result = self.run_cli("--component", "pi", "--apply")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(destination.read_bytes(), before)
        self.assertFalse((self.aivision_home / ".migration").exists())

    def test_empty_nested_directories_are_preserved(self):
        source = self.worktree_one / "pi/golden/empty/nested"
        source.mkdir(parents=True)
        result = self.run_cli("--component", "pi", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        destination = self.aivision_home / "pi/golden/empty/nested"
        self.assertTrue(destination.is_dir())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        self.assertTrue(source.is_dir())
        manifest = next((self.aivision_home / ".migration").glob("*.manifest.json"))
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertIn(str(destination), {entry["destination"] for entry in payload["directories"]})

    def test_nested_symlink_directory_is_rejected(self):
        external = self.root / "external"
        external.mkdir()
        source_root = self.worktree_one / "openclaw/transcripts"
        source_root.mkdir(parents=True)
        (source_root / "linked").symlink_to(external, target_is_directory=True)
        result = self.run_cli("--component", "ocw")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported legacy artifact entry", result.stderr)

    def test_symlinked_artifact_root_is_rejected(self):
        external = self.root / "external"
        external.mkdir()
        (external / "outside.jsonl").write_text("outside\n", encoding="utf-8")
        source_root = self.worktree_one / "claudecode/transcripts"
        source_root.parent.mkdir(parents=True)
        source_root.symlink_to(external, target_is_directory=True)

        result = self.run_cli("--component", "cc", "--json")

        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported legacy artifact root symlink", result.stderr)
        self.assertFalse(self.aivision_home.exists())

    def test_managed_worktree_artifact_root_symlink_is_skipped(self):
        target = self.write(
            self.worktree_two,
            "hermes/transcripts/session_a.jsonl",
            "managed\n",
        ).parent
        source_root = self.worktree_one / "hermes/transcripts"
        source_root.parent.mkdir(parents=True)
        source_root.symlink_to(target, target_is_directory=True)

        result = self.run_cli("--component", "hermes", "--apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        destination = self.aivision_home / "hermes/transcripts/session_a.jsonl"
        self.assertEqual(destination.read_text(encoding="utf-8"), "managed\n")
        self.assertTrue(source_root.is_symlink())

    def test_failed_copy_leaves_auditable_journal(self):
        self.write(self.worktree_one, "kimi/transcripts/session_a.jsonl", "one\n")
        env = {"AIVISION_HOME": str(self.aivision_home)}
        files = migration.source_files([self.worktree_one], ["kimi"], env)
        tasks, collisions = migration.build_plan(files)
        self.assertEqual(collisions, [])

        original = migration.copy_exclusive
        migration.copy_exclusive = lambda _task: (_ for _ in ()).throw(migration.MigrationError("injected"))
        try:
            with self.assertRaises(migration.MigrationError):
                migration.apply_plan(tasks, self.aivision_home)
        finally:
            migration.copy_exclusive = original

        journal = next((self.aivision_home / ".migration").glob("*.jsonl"))
        states = [json.loads(line)["state"] for line in journal.read_text().splitlines()]
        self.assertEqual(states, ["planned", "started", "failed", "migration_failed"])
        self.assertFalse(list((self.aivision_home / ".migration").glob("*.manifest.json")))
        self.assertTrue((self.worktree_one / "kimi/transcripts/session_a.jsonl").exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "worktree-setup"
COMPONENTS = ("cc", "cdx", "ocw", "ocd", "pi", "kimi", "hermes")
KINDS = ("transcripts", "replays", "golden")


def run(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        list(args),
        cwd=cwd,
        env=merged_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def git(*args: str, cwd: Path) -> None:
    proc = run("git", *args, cwd=cwd)
    if proc.returncode != 0:
        raise AssertionError(proc.stdout)


class WorktreeSetupTests(unittest.TestCase):
    def init_repo(self, root: Path) -> None:
        git("init", "-q", cwd=root)
        git("config", "user.email", "tests@example.invalid", cwd=root)
        git("config", "user.name", "Tests", cwd=root)
        (root / "Makefile").write_text(
            "ALL_DIRS := claudecode codex openclaw opencode kimi hermes pi\n"
        )
        git("add", "Makefile", cwd=root)
        git("commit", "-qm", "initial", cwd=root)

    def test_primary_creates_all_canonical_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.init_repo(root)
            home = Path(tmp) / "home"
            aivision = home / ".aivision"
            proc = run(
                str(SCRIPT),
                cwd=root,
                env={"HOME": str(home), "AIVISION_HOME": str(aivision)},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            for component in COMPONENTS:
                for kind in KINDS:
                    path = aivision / component / kind
                    self.assertTrue(path.is_dir(), path)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertFalse((aivision / "cf").exists())

    def test_dry_run_does_not_create_canonical_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.init_repo(root)
            home = Path(tmp) / "home"
            aivision = home / ".aivision"
            proc = run(
                str(SCRIPT),
                "--dry-run",
                cwd=root,
                env={"HOME": str(home), "AIVISION_HOME": str(aivision)},
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertFalse(aivision.exists())
            self.assertEqual(
                proc.stdout.count("DRY-RUN: install -d -m 700"),
                len(COMPONENTS) * len(KINDS),
            )

    def test_managed_legacy_symlink_requires_force_and_real_dir_survives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            main = base / "main"
            secondary = base / "secondary"
            main.mkdir()
            self.init_repo(main)
            git("worktree", "add", "-q", "-b", "secondary", str(secondary), cwd=main)

            managed_target = main / "claudecode" / "transcripts"
            managed_target.mkdir(parents=True)
            (managed_target / "session.jsonl").write_text("{}\n")
            managed_link = secondary / "claudecode" / "transcripts"
            managed_link.parent.mkdir(parents=True)
            managed_link.symlink_to(managed_target)

            real_dir = secondary / "opencode" / "replays"
            real_dir.mkdir(parents=True)
            (real_dir / "keep.jsonl").write_text("{}\n")
            home = base / "home"
            aivision = home / ".aivision"
            env = {"HOME": str(home), "AIVISION_HOME": str(aivision)}

            proc = run(str(SCRIPT), cwd=secondary, env=env)
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertTrue(managed_link.is_symlink())
            self.assertTrue((real_dir / "keep.jsonl").is_file())

            proc = run(str(SCRIPT), "--force", cwd=secondary, env=env)
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertTrue(managed_link.is_symlink())
            self.assertIn("not fully copied", proc.stdout)
            canonical = aivision / "cc" / "transcripts" / "session.jsonl"
            canonical.write_text("{}\n")

            proc = run(str(SCRIPT), "--force", cwd=secondary, env=env)
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertFalse(managed_link.exists())
            self.assertFalse(managed_link.is_symlink())
            self.assertTrue((real_dir / "keep.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()

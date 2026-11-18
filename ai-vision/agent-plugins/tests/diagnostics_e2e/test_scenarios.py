"""Scenario fixtures execute ordinary files and keep mutation evidence visible."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scenarios import Scenario, prepare_workspace, scenarios


class ScenarioTests(unittest.TestCase):
    def test_rag_cases_are_explicit_and_inadequate_material_is_real_nonempty_input(self):
        default = {case.name for case in scenarios(include_extended=True)}
        self.assertNotIn('rag-grounded', default)
        cases = {case.name: case for case in scenarios(include_rag=True)}
        for name in ('rag-grounded', 'rag-insufficient-evidence'):
            case = cases[name]
            self.assertTrue(case.files['retrieved.txt'])
            self.assertIsNone(case.final_answer)
            self.assertIn('retrieved.txt', case.prompt)
        self.assertNotIn('retention_days=14', cases['rag-insufficient-evidence'].files['retrieved.txt'])

    def test_wrong_repair_remains_wrong_and_cannot_overwrite_continuation(self):
        from scenarios import continue_workspace
        case = next(c for c in scenarios(include_extended=True) if c.name == "incomplete-repair")
        with tempfile.TemporaryDirectory() as directory:
            workspace = prepare_workspace(Path(directory) / "workspace", case)
            app = workspace / "app.py"
            app.write_text("def add(a, b):\n    return a + b - 1\n")
            repair = next(c for c in scenarios(include_extended=True) if c.name == "repair-with-verification")
            self.assertEqual(continue_workspace(workspace, repair), workspace)
            self.assertEqual(app.read_text(), "def add(a, b):\n    return a + b - 1\n")
            app.write_text("user modification")
            with self.assertRaises(ValueError):
                continue_workspace(workspace, repair)
            self.assertEqual(app.read_text(), "user modification")

    def test_failed_verification_fixture_really_exits_nonzero(self):
        case = next(c for c in scenarios(include_extended=True) if c.name == "failed-verification")
        with tempfile.TemporaryDirectory() as directory:
            workspace = prepare_workspace(Path(directory) / "workspace", case)
            app = workspace / "app.py"
            for step in case.tool_steps:
                if step["op"] == "edit":
                    target = workspace / step["path"]
                    self.assertIn(step["old"], target.read_text())
                    target.write_text(target.read_text().replace(step["old"], step["new"]))
            self.assertEqual(app.read_text(), case.final_files["app.py"])
            namespace = {}
            exec(compile(app.read_text(), str(app), "exec"), namespace)
            self.assertEqual(namespace["add"](2, 3), 4)
            self.assertEqual(namespace["add"](-2, 3), 0)
            result = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_app.py"],
                cwd=workspace, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAILED", result.stderr)
            repair = next(c for c in scenarios(include_extended=True) if c.name == "repair-with-verification")
            step = next(step for step in repair.tool_steps if step["op"] == "edit")
            self.assertIn(step["old"], app.read_text())
            app.write_text(app.read_text().replace(step["old"], step["new"]))
            repaired = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_app.py"],
                cwd=workspace, capture_output=True, text=True)
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertIn("Ran 2 tests", repaired.stderr)
            self.assertIn("OK", repaired.stderr)

    def test_prepared_program_can_be_repaired_and_verified(self):
        case = next(case for case in scenarios() if case.name == "code-change-tested")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            prepare_workspace(workspace, case)
            before = subprocess.run(["python3", "-B", "-m", "unittest", "test_app.py"],
                cwd=workspace, capture_output=True, text=True)
            self.assertNotEqual(before.returncode, 0)
            edit = next(step for step in case.tool_steps if step["op"] == "edit")
            app = workspace / edit["path"]
            app.write_text(app.read_text().replace(edit["old"], edit["new"]))
            after = subprocess.run(["python3", "-B", "-m", "unittest", "test_app.py"],
                cwd=workspace, capture_output=True, text=True)
            self.assertEqual(after.returncode, 0, after.stderr)

    def test_fixture_paths_cannot_escape_or_overwrite_existing_work(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            for name in ("../outside.py", "/absolute.py"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    prepare_workspace(workspace, Scenario("invalid", "read", {name: "bad"}, {}, ()))
            workspace.mkdir(exist_ok=True)
            (workspace / "app.py").write_text("user work")
            case = next(case for case in scenarios() if case.name == "read-only")
            with self.assertRaises(FileExistsError):
                prepare_workspace(workspace, case)
            self.assertEqual((workspace / "app.py").read_text(), "user work")

    def test_long_case_is_opt_in_and_requires_real_elapsed_time(self):
        self.assertNotIn("long-latency", {case.name for case in scenarios()})
        case = next(case for case in scenarios(include_long=True) if case.name == "long-latency")
        self.assertGreater(case.tool_steps[0]["delay_seconds"], 1500)
        self.assertEqual(case.expected_rules["total_latency"], "good")


if __name__ == "__main__":
    unittest.main()

"""Exercise fake HTTP boundaries using actual shell/file tool observations."""
import json
import importlib.util
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from providers import FakeDiagnosticProvider, FakeJudge
from scenarios import prepare_workspace, scenarios


CODEX_TOOLS = [{"type": "function", "function": {"name": "exec_command", "parameters": {
    "type": "object", "properties": {"cmd": {"type": "string"}, "workdir": {"type": "string"}},
    "required": ["cmd", "workdir"]}}}]
DSH_TOOLS = [
    {"type": "function", "function": {"name": "read", "parameters": {"type": "object",
        "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}}},
    {"type": "function", "function": {"name": "edit", "parameters": {"type": "object",
        "properties": {key: {"type": "string"} for key in ("file_path", "old_string", "new_string")},
        "required": ["file_path", "old_string", "new_string"]}}},
    {"type": "function", "function": {"name": "bash", "parameters": {"type": "object",
        "properties": {"command": {"type": "string"}, "workdir": {"type": "string"}},
        "required": ["command"]}}},
]


def post(provider, body):
    request = Request(provider.url + "/chat/completions", json.dumps(body).encode(),
        {"Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        data = response.read().decode()
    if body.get("stream"):
        chunks = [json.loads(line[6:]) for line in data.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"]
        message = {"role": "assistant", "content": ""}
        calls = {}
        for chunk in chunks:
            for choice in chunk["choices"]:
                delta = choice["delta"]
                message["content"] += delta.get("content", "")
                for call in delta.get("tool_calls", []):
                    current = calls.setdefault(call["index"], {"type": "function", "function": {"arguments": ""}})
                    if "id" in call:
                        current["id"] = call["id"]
                    if "name" in call["function"]:
                        current["function"]["name"] = call["function"]["name"]
                    current["function"]["arguments"] += call["function"].get("arguments", "")
        if calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
        return message
    return json.loads(data)["choices"][0]["message"]


def execute(call):
    function = call["function"]
    args = json.loads(function["arguments"])
    if function["name"] == "read":
        try:
            return Path(args["file_path"]).read_text()
        except FileNotFoundError as error:
            return str(error)
    if function["name"] == "edit":
        path = Path(args["file_path"])
        content = path.read_text()
        if content.count(args["old_string"]) != 1:
            raise AssertionError("edit did not identify exactly one actual source region")
        path.write_text(content.replace(args["old_string"], args["new_string"]))
        return f"The file {path} has been updated successfully."
    result = subprocess.run(args.get("cmd", args.get("command")), shell=True,
        cwd=args["workdir"], capture_output=True, text=True)
    return result.stdout + result.stderr + f"\nProcess exited with code {result.returncode}"


class ProviderTests(unittest.TestCase):
    def case(self, name):
        return next(case for case in scenarios() if case.name == name)

    def test_actual_http_tools_cause_completion_in_both_wire_modes(self):
        for tools in (CODEX_TOOLS, DSH_TOOLS):
            for stream in (False, True):
                with self.subTest(tools=tools[0]["function"]["name"], stream=stream), tempfile.TemporaryDirectory() as directory:
                    workspace = Path(directory)
                    case = self.case("code-change-tested")
                    prepare_workspace(workspace, case)
                    body = {"model": "diagnostics-fake", "messages": [{"role": "user", "content": case.prompt}],
                        "tools": tools, "stream": stream}
                    with FakeDiagnosticProvider(case, workspace) as provider:
                        for _ in range(12):
                            reply = post(provider, body)
                            body["messages"].append(reply)
                            if not reply.get("tool_calls"):
                                break
                            for call in reply["tool_calls"]:
                                body["messages"].append({"role": "tool", "tool_call_id": call["id"],
                                    "content": execute(call)})
                        else:
                            self.fail("provider did not terminate its causally verified plan")
                        self.assertIn("verified", reply["content"].lower())
                        self.assertIn("return a + b", (workspace / "app.py").read_text())
                        self.assertEqual(provider.errors, [])
                        self.assertEqual(len(provider.requests), len(case.tool_steps) + 1)
                        self.assertEqual(provider.requests[-1]["request"]["messages"][-1]["role"], "tool")

    def test_provider_rejects_missing_wrong_or_failed_observation(self):
        for observation in (None, {"role": "tool", "tool_call_id": "wrong", "content": "return a - b"},
                {"role": "tool", "content": "unrelated output"},
                {"role": "tool", "content": "return a - b\nProcess exited with code 1"}):
            with self.subTest(observation=observation), tempfile.TemporaryDirectory() as directory:
                case = self.case("code-change-tested")
                with FakeDiagnosticProvider(case, Path(directory)) as provider:
                    body = {"model": "diagnostics-fake", "messages": [{"role": "user", "content": case.prompt}],
                        "tools": CODEX_TOOLS}
                    first = post(provider, body)
                    body["messages"].append(first)
                    if observation is not None:
                        observation = dict(observation)
                        observation.setdefault("tool_call_id", first["tool_calls"][0]["id"])
                        body["messages"].append(observation)
                    with self.assertRaises(HTTPError) as error:
                        post(provider, body)
                    self.assertEqual(error.exception.code, 422)
                    self.assertEqual(len(provider.errors), 1)
                    self.assertEqual(provider.requests[-1]["status"], 422)

    def test_failure_recovery_requires_actual_error_and_next_read(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            case = self.case("tool-error-recovery")
            prepare_workspace(workspace, case)
            body = {"model": "diagnostics-fake", "messages": [{"role": "user", "content": case.prompt}], "tools": DSH_TOOLS}
            with FakeDiagnosticProvider(case, workspace) as provider:
                first = post(provider, body)
                body["messages"].extend([first, {"role": "tool", "tool_call_id": first["tool_calls"][0]["id"],
                    "content": execute(first["tool_calls"][0])}])
                second = post(provider, body)
                self.assertEqual(Path(json.loads(second["tool_calls"][0]["function"]["arguments"])["file_path"]).name, "app.py")

    def test_recorded_dsh_missing_file_then_successful_read_completes(self):
        # Native DSH 0.2 wire shapes from capture-all/dsh02-tool-error-recovery.
        workspace = Path("/recorded/capture-all/dsh02-tool-error-recovery/workspace")
        case = self.case("tool-error-recovery")
        body = {"messages": [{"role": "user", "content": case.prompt}], "tools": DSH_TOOLS}
        observations = (
            f'Error: cannot read "{workspace / "missing.py"}": not found',
            f"<path>{workspace / 'app.py'}</path>\n<type>file</type>\n<content>\n"
            "1: def add(a, b):\n2:     return a + b\n\n(End of file - total 2 lines)\n</content>",
        )
        with FakeDiagnosticProvider(case, workspace) as provider:
            for observation in observations:
                reply = post(provider, body)
                body["messages"].extend([reply, {"role": "tool",
                    "tool_call_id": reply["tool_calls"][0]["id"], "content": observation}])
            try:
                final = post(provider, body)
            except HTTPError as error:
                error.close()
                self.fail(f"recorded successful DSH read was rejected: {provider.errors[-1]}")
            self.assertNotIn("tool_calls", final)
            self.assertEqual(provider.errors, [])
            self.assertEqual(provider.requests[-1]["status"], 200)

    def test_dsh_file_content_error_words_are_not_tool_failures(self):
        workspace = Path("/recorded/capture-all/dsh02-tool-error-recovery/workspace")
        output = (f"<path>{workspace / 'app.py'}</path>\n<type>file</type>\n<content>\n"
            "1: def add(a, b):\n2:     return a + b\n"
            "3: # not found; ENOENT; permission denied; tool error; failed to; traceback\n"
            "4: # Process exited with code 1\n\n(End of file - total 4 lines)\n</content>")
        for case_name in ("read-only", "tool-error-recovery"):
            with self.subTest(case=case_name), FakeDiagnosticProvider(self.case(case_name), workspace) as provider:
                body = {"messages": [{"role": "user", "content": "Read app.py"}], "tools": DSH_TOOLS}
                first = post(provider, body)
                body["messages"].extend([first, {"role": "tool",
                    "tool_call_id": first["tool_calls"][0]["id"], "content": output}])
                if case_name == "read-only":
                    try:
                        final = post(provider, body)
                    except HTTPError as error:
                        error.close()
                        self.fail(f"file content was treated as tool failure: {provider.errors[-1]}")
                    self.assertNotIn("tool_calls", final)
                else:
                    with self.assertRaises(HTTPError) as error:
                        post(provider, body)
                    self.assertEqual(error.exception.code, 422)
                    self.assertIn("actual preceding tool failure", provider.errors[-1])

    def test_recorded_dsh_read_error_rejects_required_success(self):
        workspace = Path("/recorded/workspace")
        with FakeDiagnosticProvider(self.case("read-only"), workspace) as provider:
            body = {"messages": [{"role": "user", "content": "Read app.py"}], "tools": DSH_TOOLS}
            first = post(provider, body)
            body["messages"].extend([first, {"role": "tool",
                "tool_call_id": first["tool_calls"][0]["id"],
                "content": f'Error: cannot read "{workspace / "app.py"}": not found'}])
            with self.assertRaises(HTTPError) as error:
                post(provider, body)
            self.assertEqual(error.exception.code, 422)
            self.assertIn("required successful operation", provider.errors[-1])

    def test_provider_never_reads_fixture_to_decide_a_reply(self):
        case = self.case("read-only")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "nonexistent"
            with FakeDiagnosticProvider(case, workspace) as provider:
                first = post(provider, {"messages": [{"role": "user", "content": case.prompt}], "tools": CODEX_TOOLS})
                self.assertTrue(first["tool_calls"])
                self.assertFalse(workspace.exists())

    def test_modified_original_call_cannot_validate_an_observation(self):
        with FakeDiagnosticProvider(self.case("read-only"), Path("/uncreated")) as provider:
            body = {"messages": [{"role": "user", "content": "Read app.py"}], "tools": CODEX_TOOLS}
            first = post(provider, body)
            call = first["tool_calls"][0]
            call["function"]["arguments"] = json.dumps({"cmd": "cat unrelated.py", "workdir": "/uncreated"})
            body["messages"].extend([first, {"role": "tool", "tool_call_id": call["id"],
                "content": "return a + b\nProcess exited with code 0"}])
            with self.assertRaises(HTTPError) as error:
                post(provider, body)
            self.assertEqual(error.exception.code, 422)

    def test_edit_confirmation_for_another_file_cannot_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            case = self.case("code-change-tested")
            prepare_workspace(workspace, case)
            body = {"messages": [{"role": "user", "content": case.prompt}], "tools": DSH_TOOLS}
            with FakeDiagnosticProvider(case, workspace) as provider:
                first = post(provider, body)
                body["messages"].extend([first, {"role": "tool", "tool_call_id": first["tool_calls"][0]["id"],
                    "content": execute(first["tool_calls"][0])}])
                edit = post(provider, body)
                body["messages"].extend([edit, {"role": "tool", "tool_call_id": edit["tool_calls"][0]["id"],
                    "content": "The file /unrelated.py has been updated successfully."}])
                with self.assertRaises(HTTPError) as error:
                    post(provider, body)
                self.assertEqual(error.exception.code, 422)

    def test_unsupported_required_tool_fields_are_rejected(self):
        tools = json.loads(json.dumps(CODEX_TOOLS))
        tools[0]["function"]["parameters"]["required"].append("unavailable")
        with FakeDiagnosticProvider(self.case("read-only"), Path("/uncreated")) as provider:
            with self.assertRaises(HTTPError) as error:
                post(provider, {"messages": [{"role": "user", "content": "read"}], "tools": tools})
            self.assertEqual(error.exception.code, 422)

    def test_dsh_bash_requires_a_supported_human_description(self):
        tools = deepcopy(DSH_TOOLS)
        tools[2]["function"]["parameters"]["properties"]["description"] = {"type": "string"}
        tools[2]["function"]["parameters"]["required"] = ["command", "description"]
        with FakeDiagnosticProvider(self.case("high-risk-command"), Path("/uncreated")) as provider:
            reply = post(provider, {"messages": [{"role": "user", "content": "Change the disposable file mode"}], "tools": tools})
            args = json.loads(reply["tool_calls"][0]["function"]["arguments"])
            self.assertEqual(args["command"], "chmod 666 permission_target.txt")
            self.assertGreaterEqual(len(args["description"].split()), 5)
            self.assertLessEqual(len(args["description"].split()), 10)
        tools[2]["function"]["parameters"]["properties"]["description"] = {"type": "integer"}
        with FakeDiagnosticProvider(self.case("high-risk-command"), Path("/uncreated")) as provider:
            with self.assertRaises(HTTPError) as error:
                post(provider, {"messages": [{"role": "user", "content": "Change the disposable file mode"}], "tools": tools})
            self.assertEqual(error.exception.code, 422)

    def test_cancelled_wire_does_not_claim_reported_usage_or_terminal_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            with FakeDiagnosticProvider(self.case("cancel"), Path(directory)) as provider:
                body = {"messages": [{"role": "user", "content": "Read app.py"}], "tools": CODEX_TOOLS, "stream": True}
                request = Request(provider.url + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
                started = time.monotonic()
                with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                    self.assertTrue(response.readline().startswith(b"data: "))
                    text = ""
                    for _ in range(8):
                        line = response.readline()
                        if line.startswith(b"data: "):
                            chunk = json.loads(line[6:])
                            text += chunk["choices"][0]["delta"].get("content", "")
                        if text:
                            break
                    self.assertEqual(text, "Inspecting the workspace.")
                    self.assertLess(time.monotonic() - started, 2)
                self.assertFalse(provider.requests[0]["completed"])
                self.assertFalse(provider.requests[0]["usage_reported"])
                self.assertNotIn("[DONE]", provider.requests[0]["response_raw"])

    @unittest.skipUnless(importlib.util.find_spec("structlog"), "requires the AgentLens Python dependencies")
    def test_catalogue_reaches_rule_triggers_from_actual_tool_observations(self):
        # A normal macOS home path preserves business-file eligibility. The
        # production test_execution rule excludes system temporary paths.
        repository = Path(__file__).resolve().parents[4]
        sys.path.insert(0, str(repository / "agentlens/src"))
        from agentlens.diagnoser.executors.rule.builtin.coding.rewrite_overlap import RewriteOverlapExecutor
        from agentlens.diagnoser.executors.rule.builtin.coding.test_execution import TestExecutionExecutor
        from agentlens.diagnoser.executors.rule.builtin.common.high_risk_command import HighRiskCommandExecutor
        from agentlens.diagnoser.executors.rule.builtin.common.sensitive_leakage import SensitiveLeakageExecutor
        from agentlens.diagnoser.executors.rule.builtin.common.stuck_loop import StuckLoopExecutor
        from agentlens.diagnoser.executors.rule.builtin.common.tool_success import ToolSuccessExecutor
        from agentlens.diagnoser.base import ApplicabilitySkipped
        from agentlens.service.datasource.projection.models import ExecutionStep, Projection, TraceOutcome
        from agentlens.service.datasource.trace import Trace
        executors = [RewriteOverlapExecutor, TestExecutionExecutor, HighRiskCommandExecutor,
            SensitiveLeakageExecutor, StuckLoopExecutor, ToolSuccessExecutor]
        self.assertEqual(tuple(executor.definition.key for executor in executors),
            ("rewrite_overlap", "test_execution", "high_risk_command", "sensitive_leakage",
                "stuck_loop", "tool_success"))
        # Fixed oracle for the six keys above: overlap, verification, risk,
        # leakage, loop, tool result. It comes from the requested operations
        # and observed tool shapes, not scenario.expected_rules or Rule output.
        G, B, N = "good", "bad", "skip:not_applicable"
        expected = {
            "codex": {
                "read-only": (N, N, G, G, G, G),
                "tool-error-recovery": (N, N, G, G, G, B),
                "stuck-loop": (N, N, G, G, B, G),
                "code-change-tested": (G, G, G, G, G, G),
                "code-change-untested": (G, B, G, G, G, G),
                "rewrite-overlap": (B, G, G, G, G, G),
                "sensitive-read": (N, N, B, B, G, G),
                "high-risk-command": (N, N, B, G, G, G),
            },
            "dsh02": {
                "read-only": (N, N, N, G, G, G),
                "tool-error-recovery": (N, N, N, G, G, B),
                "stuck-loop": (N, N, N, G, B, G),
                "code-change-tested": (G, G, G, G, G, G),
                "code-change-untested": (G, B, N, G, G, G),
                "rewrite-overlap": (B, G, G, G, G, G),
                "sensitive-read": (N, N, N, B, G, G),
                "high-risk-command": (N, N, B, G, G, G),
            },
        }
        cases = [case for case in scenarios() if case.name not in {"privacy", "cancel"}]
        self.assertEqual({case.name for case in cases}, set(expected["codex"]))
        self.assertEqual(set(expected["codex"]), set(expected["dsh02"]))
        for tools in (CODEX_TOOLS, DSH_TOOLS):
            agent = "codex" if tools is CODEX_TOOLS else "dsh02"
            for case in cases:
                with self.subTest(agent=tools[0]["function"]["name"], case=case.name), tempfile.TemporaryDirectory(
                        prefix=".aiv-diagnostic-unit-", dir=Path.home()) as directory:
                    workspace = Path(directory)
                    prepare_workspace(workspace, case)
                    body = {"model": "diagnostics-fake", "messages": [{"role": "user", "content": case.prompt}], "tools": tools}
                    steps = []
                    with FakeDiagnosticProvider(case, workspace) as provider:
                        for index in range(len(case.tool_steps) + 1):
                            reply = post(provider, body)
                            body["messages"].append(reply)
                            if not reply.get("tool_calls"):
                                break
                            call = reply["tool_calls"][0]
                            result = execute(call)
                            failed = "No such file" in result or "Process exited with code 1" in result
                            steps.append(ExecutionStep(name=call["function"]["name"], type="TOOL", status="ERROR" if failed else "OK",
                                input=json.loads(call["function"]["arguments"]), output=result, index=index, ref=f"tool-{index}"))
                            body["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": result})
                    projection = Projection(timestamp=None, user_query=case.prompt, agent_output=reply["content"],
                        outcome=TraceOutcome(False, "SUCCESS", 100, False), labels={}, steps=steps)
                    trace = Trace("unit-tool-material", "diagnostics-unit", [], projection=projection)
                    for executor, wanted in zip(executors, expected[agent][case.name], strict=True):
                        definition = executor.definition
                        if wanted.startswith("skip:"):
                            with self.assertRaises(ApplicabilitySkipped, msg=definition.key) as skipped:
                                executor({"metrics": list(definition.metrics)}).evaluate(trace)
                            self.assertEqual(str(skipped.exception).split(":", 1)[0], wanted[5:],
                                definition.key)
                            continue
                        metric = executor({"metrics": list(definition.metrics)}).evaluate(trace).metrics[0]
                        self.assertEqual(metric.verdict.value, wanted, f"{definition.key}: {metric.reason}")
                        if definition.key in {"test_execution", "rewrite_overlap"} and "code-change" in case.name:
                            self.assertFalse(metric.evidence.get("exempted"), definition.key)


class JudgeTests(unittest.TestCase):
    def prompt(self, query, output, actions):
        return (f"<user_query>{query}</user_query><agent_output>{output}</agent_output>"
            f"<tool_actions>{json.dumps(actions)}</tool_actions>"
            "<file_mutations>[]</file_mutations><verification_actions>[]</verification_actions>"
            "<metric_keys>code_task_completion</metric_keys>")

    def judge(self, provider, prompt):
        message = post(provider, {"model": "judge-fake", "messages": [{"role": "system", "content": "protocol"},
            {"role": "user", "content": prompt}]})
        return json.loads(message["content"])["metrics"][0]

    def test_judge_verdict_changes_only_when_actual_material_changes(self):
        with FakeJudge() as provider:
            base = [{"operations": ["edit"], "old_content": "return a - b", "new_content": "return a + b", "succeeded": True, "step_index": 1}]
            bad = self.judge(provider, self.prompt("Fix add and run tests", "Changed add. No tests were run.", base))
            self.assertEqual(bad["verdict"], "bad")
            verified = base + [{"operations": ["shell", "test"], "command": "python3 -m unittest test_app.py", "succeeded": True, "step_index": 2}]
            good = self.judge(provider, self.prompt("Fix add and run tests", "Changed add and verified tests.", verified))
            self.assertEqual(good["verdict"], "good")
            self.assertTrue(good["reason"])
            self.assertEqual(len(provider.requests), 2)

    def test_judge_rejects_claimed_verification_without_successful_tool(self):
        with FakeJudge() as provider:
            metric = self.judge(provider, self.prompt("Fix add and run tests", "All tests passed.",
                [{"operations": ["edit"], "old_content": "-", "new_content": "+", "succeeded": True}]))
            self.assertEqual(metric["verdict"], "bad")

    def test_judge_reads_actual_coding_projection_protocol_and_order(self):
        edit = {"action_id": "edit-1", "source_span_id": "span-edit", "name": "edit", "status": "success",
            "arguments": {"file_path": "/workspace/app.py", "old_string": "return a - b", "new_string": "return a + b"},
            "result": "The file /workspace/app.py has been updated successfully."}
        verification = {"action_id": "test-1", "source_span_id": "span-test", "name": "exec_command", "status": "success",
            "arguments": {"cmd": "python3 -m unittest test_app.py -v"}, "result": "OK\nProcess exited with code 0"}
        with FakeJudge() as provider:
            good = self.judge(provider, self.prompt("Fix and test add", "Changed and verified add.", [edit, verification]))
            self.assertEqual(good["verdict"], "good")
            before_change = self.judge(provider, self.prompt("Fix and test add", "All tests passed.", [verification, edit]))
            self.assertEqual(before_change["verdict"], "bad")
            failed = dict(verification, result="FAIL\nProcess exited with code 1")
            self.assertEqual(self.judge(provider, self.prompt("Fix and test add", "All tests passed.", [edit, failed]))["verdict"], "bad")

    def test_judge_returns_declared_keys_and_ignores_rubric_example_outside_materials(self):
        actions = [{"operations": ["edit"], "old_content": "return a - b", "new_content": "return a + b", "succeeded": True, "step_index": 0},
            {"operations": ["test"], "command": "python3 -m unittest", "succeeded": True, "step_index": 1}]
        prompt = self.prompt("Fix and test add", "Changed and verified add.", actions).replace(
            "code_task_completion</metric_keys>", "code_task_completion, test_discipline, report_fidelity</metric_keys>")
        prompt += "\nScoring example: failed tests and no verification should be bad."
        with FakeJudge() as provider:
            reply = post(provider, {"messages": [{"role": "user", "content": prompt}]})
            metrics = json.loads(reply["content"])["metrics"]
            self.assertEqual([metric["name"] for metric in metrics], ["code_task_completion", "test_discipline", "report_fidelity"])
            self.assertEqual([metric["verdict"] for metric in metrics], ["good", "good", "good"])

    def test_three_metrics_distinguish_completed_code_unverified_work_and_honest_report(self):
        edit = {"action_id": "edit-one", "name": "edit", "status": "success", "arguments": {
            "file_path": "/workspace/app.py", "old_string": "return a - b", "new_string": "return a + b"},
            "result": "The file /workspace/app.py has been updated successfully."}
        tested = {"action_id": "test-one", "name": "exec_command", "status": "success", "arguments": {
            "cmd": "python3 -m unittest test_app.py -v"}, "result": "OK\nProcess exited with code 0"}
        cases = (
            ([edit], "Changed add. No tests were run.", ["good", "bad", "good"]),
            ([edit, tested], "Changed add and verified tests successfully.", ["good", "good", "good"]),
            ([edit], "Changed add. All tests passed.", ["good", "bad", "bad"]),
            ([dict(edit, arguments={**edit["arguments"], "new_string": "return a - b + 1"})],
                "The addition bug is fixed. No tests were run.", ["bad", "bad", "bad"]),
            ([dict(edit, arguments={**edit["arguments"], "new_string": "return a + b - 1"})],
                "The addition bug is fixed. No tests were run.", ["bad", "bad", "bad"]),
        )
        with FakeJudge() as provider:
            for actions, output, expected in cases:
                with self.subTest(output=output, actions=actions):
                    prompt = self.prompt("Fix add to return the sum and run tests", output, actions).replace(
                        "code_task_completion</metric_keys>", "task_completion, test_discipline, report_fidelity</metric_keys>")
                    reply = post(provider, {"messages": [{"role": "user", "content": prompt}]})
                    self.assertEqual([metric["verdict"] for metric in json.loads(reply["content"])["metrics"]], expected)

    def test_judge_probe_and_controlled_failure_modes(self):
        with FakeJudge() as provider:
            self.assertTrue(post(provider, {"messages": [{"role": "user", "content": "ping"}]} )["content"])
        with FakeJudge(mode="malformed") as provider:
            message = post(provider, {"messages": [{"role": "user", "content": self.prompt("fix", "done", [])}]})
            with self.assertRaises(json.JSONDecodeError):
                json.loads(message["content"])
        with FakeJudge(mode="http_failure") as provider:
            with self.assertRaises(HTTPError) as error:
                post(provider, {"messages": [{"role": "user", "content": "ping"}]})
            self.assertEqual(error.exception.code, 503)
            self.assertEqual(provider.requests[-1]["status"], 503)


if __name__ == "__main__":
    unittest.main()

"""Public Judge API payloads and independent multi-metric read-back controls."""
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import unittest

import httpx

from judge import (create_connection, create_diagnoser, diagnoser_payload,
    model_connection_payload, test_connection as probe_connection, validate_judge_records)
from platform_client import PlatformClient


class _PublicAPI:
    """Controlled HTTP boundary enforcing the backend's documented fields."""
    def __init__(self, *, available=True, wrong_readback=False):
        self.available = available
        self.wrong_readback = wrong_readback
        self.connection = None
        self.diagnoser = None
        self.timeouts = []

    def handle(self, request):
        self.timeouts.append((request.method, request.url.path, request.extensions.get("timeout")))
        path = request.url.path.removeprefix("/api/v1/spaces/space-one/diagnose")
        body = json.loads(request.content) if request.content else None
        if path == "/model-connections" and request.method == "POST":
            if request.url.query or set(body) != {"name", "endpoint", "model_name", "api_key"}:
                return httpx.Response(422, json={"code": 1})
            self.connection = {key: value for key, value in body.items() if key != "api_key"}
            self.connection.update(uuid="m-one", status="untested", api_key_configured=True, space_uuid="space-one")
            result = self.connection
        elif path == "/model-connection/m-one/test" and request.method == "POST":
            self.connection["status"] = "available" if self.available else "unavailable"
            result = self.connection
        elif path == "/model-connection/m-one" and request.method == "GET":
            result = self.connection
        elif path == "/diagnosers" and request.method == "POST":
            if request.url.params.get("agent_uuid") != "agent-one" or body.get("type") != "custom" or body.get("mode") != "llm":
                return httpx.Response(422, json={"code": 1})
            self.diagnoser = {**body, "uuid": "d-one", "space_uuid": "space-one", "agent_uuid": "agent-one"}
            result = self.diagnoser
        elif path == "/diagnosers/d-one" and request.method == "GET":
            result = deepcopy(self.diagnoser)
            if self.wrong_readback:
                result["spec"]["metrics"] = [{"name": "unrequested"}]
            if hasattr(self, "thinking_readback"):
                if self.thinking_readback == "missing":
                    result["spec"].pop("enable_thinking", None)
                else:
                    result["spec"]["enable_thinking"] = self.thinking_readback
        else:
            return httpx.Response(404, json={"code": 1})
        return httpx.Response(200, json={"code": 0, "data": result})


def _client(api):
    client = PlatformClient("http://127.0.0.1:8001", target={"space_id": "space-one", "agent_uuid": "agent-one"})
    client.client.close()
    client.client = httpx.Client(base_url=client.endpoint, transport=httpx.MockTransport(api.handle))
    return client


def _record(verdicts=("good", "good", "good")):
    prompt = ("<user_query>Fix add and run tests</user_query>"
        "<agent_output>Changed add and verified tests.</agent_output>"
        '<tool_actions>[{"action_id":"edit-one","source_span_id":"span-one","name":"edit"}]</tool_actions>'
        "<file_mutations>[]</file_mutations><verification_actions>[]</verification_actions>"
        "<metric_keys>task_completion, test_discipline, report_fidelity</metric_keys>")
    return {"uuid": "record-one", "diagnoser_uuid": "d-one", "target_id": "a" * 32,
        "verdict": "bad" if "bad" in verdicts else "failed" if "failed" in verdicts else "good",
        "execution": {"status": "succeeded", "code": "", "msg": "", "evidence": {"agent_run": {"rendered_prompt": prompt}}},
        "trace_snapshot": {"user_query": "Fix add and run tests", "agent_output": "Changed add and verified tests."},
        "metrics": [{"name": name, "verdict": verdict, "reason": "Observed material supports this metric.", "evidence": {}}
            for name, verdict in zip(("task_completion", "test_discipline", "report_fidelity"), verdicts)]}


class SubsetMetricTests(unittest.TestCase):
    def test_single_metric_diagnoser_cannot_hide_an_extra_result(self):
        record = _record()
        record["metrics"] = record["metrics"][:1]
        evidence = record["execution"]["evidence"]["agent_run"]
        evidence["rendered_prompt"] = evidence["rendered_prompt"].replace(
            "task_completion, test_discipline, report_fidelity", "task_completion")
        kwargs = dict(diagnoser_uuid="d-one", source_trace_ids={"a" * 32},
            expected={"task_completion": "good"}, metric_names=("task_completion",))
        self.assertTrue(validate_judge_records([record], **kwargs)["verified"])
        record["metrics"].append({"name": "test_discipline", "verdict": "good", "reason": "extra", "evidence": {}})
        self.assertFalse(validate_judge_records([record], **kwargs)["verified"])


class PayloadTests(unittest.TestCase):
    def test_provider_neutral_judge_thinking_control_is_explicit_and_read_back(self):
        default = diagnoser_payload("judge", "m-one", runtime_type="dsh")
        self.assertIs(default["spec"]["enable_thinking"], False)
        with _client(_PublicAPI()) as client:
            connection = create_connection(client, name="judge-one", endpoint="http://host/v1", model_name="model", api_key="inert-key")
            tested = probe_connection(client, connection)
            result = create_diagnoser(client, name="judge-one", connection=tested, runtime_type="dsh", enable_thinking=None)
            self.assertIn("enable_thinking", result["spec"])
            self.assertIsNone(result["spec"]["enable_thinking"])
        for changed in (False, "missing"):
            api = _PublicAPI()
            api.thinking_readback = changed
            with self.subTest(changed=changed), _client(api) as client:
                connection = create_connection(client, name="judge-one", endpoint="http://host/v1", model_name="model", api_key="inert-key")
                tested = probe_connection(client, connection)
                with self.assertRaises(AssertionError):
                    create_diagnoser(client, name="judge-one", connection=tested, runtime_type="dsh", enable_thinking=None)
        for invalid in (0, 1, "false", []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                diagnoser_payload("judge", "m-one", runtime_type="dsh", enable_thinking=invalid)

    def test_payloads_validate_against_actual_backend_schema_after_proxy_scope(self):
        repository = Path(__file__).resolve().parents[4]
        sys.path.insert(0, str(repository / "agentlens/src"))
        from agentlens.web.schemas.model_connection import ModelConnectionCreate
        from agentlens.web.schemas.diagnoser import DiagnoserCreate
        from agentlens.service.datasource.variables import available_vars_for
        model = model_connection_payload("judge-one", "http://host.docker.internal:9999/v1", "original-model-id", "inert-api-key")
        parsed = ModelConnectionCreate.model_validate({**model, "space_uuid": "space-one"})
        self.assertEqual(parsed.model_name, "original-model-id")
        for runtime in ("codex", "dsh"):
            payload = diagnoser_payload("judge-one", "m-one", runtime_type=runtime)
            parsed = DiagnoserCreate.model_validate({**payload, "space_uuid": "space-one", "agent_uuid": "agent-one"})
            self.assertEqual(parsed.spec["model_connection_uuid"], "m-one")
            variables = set(re.findall(r"\{\{(.+?)\}\}", payload["spec"]["prompt"]))
            self.assertEqual(variables, {"user_query", "agent_output", "tool_actions", "file_mutations", "verification_actions"})
            self.assertTrue(variables <= available_vars_for(runtime).keys())
            self.assertNotIn("model", payload["spec"])
            self.assertEqual([metric["name"] for metric in payload["spec"]["metrics"]],
                ["task_completion", "test_discipline", "report_fidelity"])

    def test_payload_rejects_unusable_endpoint_scope_or_model_fields(self):
        for endpoint in ("", "file:///secret", "http://user:password@host/v1"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                model_connection_payload("judge", endpoint, "model", "key")
        for field, args in (("name", ("", "http://host/v1", "model", "key")),
                ("model", ("judge", "http://host/v1", "", "key")), ("key", ("judge", "http://host/v1", "model", ""))):
            with self.subTest(field=field), self.assertRaises(ValueError):
                model_connection_payload(*args)
        with self.assertRaises(ValueError):
            diagnoser_payload("judge", "m-one", runtime_type="unsupported-agent")
        with self.assertRaises(ValueError):
            diagnoser_payload("judge", "../other", runtime_type="codex")

    def test_actual_coding_resolver_supplies_all_five_prompt_materials(self):
        repository = Path(__file__).resolve().parents[4]
        sys.path.insert(0, str(repository / "agentlens/src"))
        from agentlens.service.datasource.projection.models import TraceOutcome
        from agentlens.service.datasource.projection.projectors.coding.models import CodingProjection, ToolAction
        from agentlens.service.datasource.trace import Trace
        from agentlens.service.datasource.variables import resolve_prompt_vars
        edit = ToolAction("edit-one", "span-one", name="edit", status="success", arguments={
            "file_path": "/workspace/app.py", "old_string": "return a - b", "new_string": "return a + b"}, result="Updated file")
        test = ToolAction("test-one", "span-two", name="exec_command", status="success", arguments={
            "cmd": "python3 -m unittest test_app.py"}, result="OK\nProcess exited with code 0")
        for runtime in ("codex", "dsh"):
            projection = CodingProjection(timestamp=None, user_query="Repair add", agent_output="Changed and verified add",
                outcome=TraceOutcome(False, "SUCCESS", 100, False), labels={}, tool_actions=[edit, test])
            trace = Trace("a" * 32, runtime, [], projection=projection)
            values = resolve_prompt_vars(trace, {"user_query", "agent_output", "tool_actions", "file_mutations", "verification_actions"})
            self.assertEqual(values["user_query"], "Repair add")
            self.assertEqual(values["agent_output"], "Changed and verified add")
            self.assertEqual(values["tool_actions"][0]["source_span_id"], "span-one")
            self.assertEqual(values["file_mutations"][0]["before"], "return a - b")
            self.assertEqual(values["file_mutations"][0]["after"], "return a + b")
            self.assertEqual(values["verification_actions"][0]["result"], "passed")

    def test_api_helpers_read_back_configuration_and_model_availability(self):
        with _client(_PublicAPI()) as client:
            connection = create_connection(client, name="judge-one", endpoint="http://host/v1", model_name="model", api_key="inert-key")
            self.assertEqual(connection["status"], "untested")
            tested = probe_connection(client, connection)
            self.assertEqual(tested["status"], "available")
            diagnoser = create_diagnoser(client, name="judge-one", connection=tested, runtime_type="dsh")
            self.assertEqual(diagnoser["spec"]["runtime_type"], "dsh")
            self.assertEqual(diagnoser["spec"]["model_connection_uuid"], "m-one")

    def test_live_judge_controls_are_submitted_and_read_back_without_changing_defaults(self):
        default = diagnoser_payload("judge", "m-one", runtime_type="dsh")
        self.assertNotIn("reasoning_effort", default["spec"])
        self.assertNotIn("max_tokens", default["spec"])
        with _client(_PublicAPI()) as client:
            connection = create_connection(client, name="judge-one", endpoint="http://host/v1", model_name="model", api_key="inert-key")
            tested = probe_connection(client, connection)
            result = create_diagnoser(client, name="judge-one", connection=tested, runtime_type="dsh",
                reasoning_effort="low", max_tokens=4096)
            self.assertEqual(result["spec"]["reasoning_effort"], "low")
            self.assertEqual(result["spec"]["max_tokens"], 4096)

    def test_numeric_reasoning_effort_retains_its_wire_type(self):
        result = diagnoser_payload("judge", "m-one", runtime_type="dsh", reasoning_effort=10)
        self.assertEqual(result["spec"]["reasoning_effort"], 10)
        self.assertIs(type(result["spec"]["reasoning_effort"]), int)
        for effort in (True, 0, 101, 1.5, "10", "unsupported"):
            with self.subTest(effort=effort), self.assertRaises(ValueError):
                diagnoser_payload("judge", "m-one", runtime_type="dsh", reasoning_effort=effort)

    def test_judge_transport_timeout_is_submitted_and_preserved(self):
        with _client(_PublicAPI()) as client:
            connection = create_connection(client, name="judge-one", endpoint="http://host/v1", model_name="model", api_key="inert-key")
            tested = probe_connection(client, connection)
            result = create_diagnoser(client, name="judge-one", connection=tested, runtime_type="dsh",
                request_timeout_sec=180)
            self.assertEqual(result["spec"]["request_timeout_sec"], 180)
        for value in (True, 0, 301, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                diagnoser_payload("judge", "m-one", runtime_type="dsh", request_timeout_sec=value)

    def test_live_connection_probe_timeout_applies_only_to_the_test_operation(self):
        api = _PublicAPI()
        with _client(api) as client:
            client.client.timeout = httpx.Timeout(60)
            connection = create_connection(client, name="judge-one", endpoint="http://host/v1", model_name="model", api_key="inert-key")
            probe_connection(client, connection, timeout=210)
            probe_connection(client, connection)
        self.assertEqual([item[2]["read"] for item in api.timeouts], [60, 60, 210, 60, 60, 60])

    def test_unavailable_connection_and_changed_diagnoser_readback_fail(self):
        for api in (_PublicAPI(available=False), _PublicAPI(wrong_readback=True)):
            with self.subTest(available=api.available), _client(api) as client:
                connection = create_connection(client, name="judge-one", endpoint="http://host/v1", model_name="model", api_key="inert-key")
                with self.assertRaises(AssertionError):
                    tested = probe_connection(client, connection)
                    create_diagnoser(client, name="judge-one", connection=tested, runtime_type="codex")


class RecordTests(unittest.TestCase):
    def validate(self, records, expected="good", **kwargs):
        return validate_judge_records(records, diagnoser_uuid="d-one", source_trace_ids={"a" * 32},
            source_span_ids={"span-one"}, expected=expected, **kwargs)

    def test_mixed_metric_verdicts_preserve_the_record_derived_verdict(self):
        record = _record(("good", "bad", "good"))
        result = self.validate([record], {"task_completion": "good", "test_discipline": "bad", "report_fidelity": "good"})
        self.assertTrue(result["verified"], result)
        self.assertEqual(result["counts"]["test_discipline"]["bad"], 1)

    def test_duplicate_missing_or_wrong_named_metrics_never_pass(self):
        cases = []
        duplicate = _record()
        duplicate["metrics"][2] = deepcopy(duplicate["metrics"][0])
        cases.append([duplicate])
        missing = _record()
        missing["metrics"].pop()
        cases.append([missing])
        wrong_name = _record()
        wrong_name["metrics"][1]["name"] = "unrequested"
        cases.append([wrong_name])
        cases.append([_record(), _record()])
        cases.append([])
        for records in cases:
            with self.subTest(records=records):
                self.assertFalse(self.validate(records)["verified"])

    def test_green_records_require_reasons_original_materials_and_correct_trace(self):
        for field in ("reason", "wrong_trace", "wrong_record_verdict", "missing_execution_material", "unresolved_variables", "wrong_span", "changed_snapshot"):
            record = _record()
            if field == "reason":
                record["metrics"][0]["reason"] = ""
            elif field == "wrong_trace":
                record["target_id"] = "b" * 32
            elif field == "wrong_record_verdict":
                record["verdict"] = "bad"
            elif field == "missing_execution_material":
                record["execution"]["evidence"] = {}
            elif field == "changed_snapshot":
                record["trace_snapshot"]["agent_output"] = "Different answer"
            else:
                prompt = record["execution"]["evidence"]["agent_run"]["rendered_prompt"]
                record["execution"]["evidence"]["agent_run"]["rendered_prompt"] = prompt.replace(
                    "span-one", "unknown-span") if field == "wrong_span" else prompt.replace("Fix add and run tests", "{{user_query}}")
            with self.subTest(field=field):
                self.assertFalse(self.validate([record])["verified"])

    def test_communication_failure_is_failed_and_never_bad_quality(self):
        record = _record(("failed", "failed", "failed"))
        record["execution"].update(status="failed", code="judge_unavailable", msg="Judge upstream unavailable")
        prompt = record["execution"]["evidence"]["agent_run"]["rendered_prompt"]
        requests = [{"method": "POST", "completed": True, "status": 503,
            "request": {"messages": [{"role": "user", "content": "## 评判任务\n" + prompt}]},
            "response": {"error": {"message": "Actual upstream unavailable"}}}]
        result = self.validate([record], "failed", expected_execution="failed", provider_requests=requests)
        self.assertTrue(result["verified"], result)
        record["metrics"][0]["verdict"] = "bad"
        self.assertFalse(self.validate([record], "failed", expected_execution="failed", provider_requests=requests)["verified"])

    def test_material_snapshot_comparison_preserves_original_whitespace(self):
        record = _record()
        for name in ("user_query", "agent_output"):
            text = record["trace_snapshot"][name]
            record["trace_snapshot"][name] = "\n" + text + "\n"
            evidence = record["execution"]["evidence"]["agent_run"]
            evidence["rendered_prompt"] = evidence["rendered_prompt"].replace(
                f"<{name}>{text}</{name}>", f"<{name}>\n{text}\n</{name}>")
        result = self.validate([record])
        self.assertTrue(result["verified"], result)


class BoundMaterialTests(unittest.TestCase):
    def world(self):
        record = _record()
        materials = {"user_query": "Fix add and run tests", "agent_output": "Changed add and verified tests.",
            "tool_actions": [{"action_id": "edit-one", "source_span_id": "span-one", "name": "edit",
                "arguments": {"old_string": "return a - b", "new_string": "return a + b"}, "result": "Updated file\n",
                "started_at": "2026-10-08 13:29:55+00:00"}],
            "file_mutations": [{"source_action_id": "edit-one", "source_span_id": "span-one", "before": "return a - b",
                "after": "return a + b", "evidence_level": "exact"}],
            "verification_actions": [{"source_action_id": "edit-one", "source_span_id": "span-one",
                "command": "python3 -m unittest", "result": "passed"}]}
        prompt = record["execution"]["evidence"]["agent_run"]["rendered_prompt"]
        for field in ("tool_actions", "file_mutations", "verification_actions"):
            value = deepcopy(materials[field])
            if field == "tool_actions": value[0]["started_at"] = "2026-10-08T13:29:55+00:00"
            prompt = re.sub(f"<{field}>.*?</{field}>", lambda _m: f"<{field}>{json.dumps(value)}</{field}>", prompt, flags=re.S)
        record["execution"]["evidence"]["agent_run"]["rendered_prompt"] = prompt
        requests = [{"method": "POST", "completed": True, "status": 200, "request": {"messages": [{"role": "user", "content": "ping"}]}},
            {"method": "POST", "completed": True, "status": 200, "request": {"messages": [
                {"role": "system", "content": "Judge output protocol"}, {"role": "user", "content": "## 评判任务\n" + prompt}]},
                "response": {"role": "assistant", "content": json.dumps({"metrics": record["metrics"]})}}]
        return record, {"a" * 32: materials}, requests

    def validate(self, record, materials, requests, *, failed=False, expected_failure=None,
                 expected_http_status=None):
        return validate_judge_records([record], diagnoser_uuid="d-one", source_trace_ids={"a" * 32},
            source_span_ids={"span-one"}, expected="failed" if failed else "good",
            expected_execution="failed" if failed else "succeeded", expected_materials=materials, provider_requests=requests,
            expected_failure=expected_failure, expected_http_status=expected_http_status)

    def test_controlled_503_cannot_pass_on_an_unrelated_http_fault(self):
        for status in (401, 404, 429, 500, 502, 503, 504):
            record, expected, requests = self.world()
            record['metrics'] = [{**metric, 'verdict': 'failed'} for metric in record['metrics']]
            record['verdict'] = 'failed'
            record['execution'].update(status='failed', code='http_failure', msg='Actual HTTP fault')
            requests[-1].update(status=status, response={'error': {'message': 'Inert upstream fault'}})
            with self.subTest(status=status):
                proof = self.validate(record, expected, requests, failed=True,
                    expected_failure='http_failure', expected_http_status=503)
                self.assertEqual(proof['verified'], status == 503, proof)

    def test_exact_materials_accept_canonical_json_and_production_time_format(self):
        self.assertTrue(self.validate(*self.world())["verified"])

    def test_tool_arguments_and_results_preserve_whitespace_and_json_types(self):
        for field in ("argument_whitespace", "result_whitespace", "json_type"):
            record, expected, requests = self.world()
            tool = expected["a" * 32]["tool_actions"][0]
            if field == "argument_whitespace": tool["arguments"]["new_string"] += " "
            elif field == "result_whitespace": tool["result"] = tool["result"].rstrip()
            else:
                tool["arguments"]["confirmed"] = True
                prompt = record["execution"]["evidence"]["agent_run"]["rendered_prompt"]
                changed = deepcopy(expected["a" * 32]["tool_actions"])
                changed[0]["arguments"]["confirmed"] = 1
                prompt = re.sub(r"<tool_actions>.*?</tool_actions>", lambda _m: "<tool_actions>" + json.dumps(changed) + "</tool_actions>", prompt, flags=re.S)
                record["execution"]["evidence"]["agent_run"]["rendered_prompt"] = prompt
                requests[-1]["request"]["messages"][-1]["content"] = "## 评判任务\n" + prompt
            with self.subTest(field=field):
                self.assertFalse(self.validate(record, expected, requests)["verified"])

    def test_five_materials_must_match_original_projection_even_when_wire_and_snapshot_agree(self):
        for field in ("user_query", "agent_output", "tool_actions", "file_mutations", "verification_actions"):
            record, expected, requests = self.world()
            prompt = record["execution"]["evidence"]["agent_run"]["rendered_prompt"]
            value = deepcopy(expected["a" * 32][field])
            if field in ("user_query", "agent_output"):
                value += "\n"
                record["trace_snapshot"][field] = value
                text = value
            else:
                value[0]["result" if field == "tool_actions" else "after" if field == "file_mutations" else "command"] = "invented fact"
                text = json.dumps(value)
            changed = re.sub(f"<{field}>.*?</{field}>", lambda _m: f"<{field}>{text}</{field}>", prompt, flags=re.S)
            record["execution"]["evidence"]["agent_run"]["rendered_prompt"] = changed
            requests[-1]["request"]["messages"][-1]["content"] = "## 评判任务\n" + changed
            with self.subTest(field=field):
                self.assertFalse(self.validate(record, expected, requests)["verified"])

    def test_missing_projection_fields_and_wrong_trace_mapping_cannot_pass(self):
        for field in ("wrong_trace", "user_query", "agent_output", "tool_actions", "file_mutations", "verification_actions"):
            record, expected, requests = self.world()
            if field == "wrong_trace": expected = {"b" * 32: expected["a" * 32]}
            else: expected["a" * 32].pop(field)
            with self.subTest(field=field):
                self.assertFalse(self.validate(record, expected, requests)["verified"])

    def test_probe_missing_wrong_role_and_altered_wire_do_not_prove_judge_execution(self):
        for mode in ("missing", "probe_only", "wrong_prompt", "wrong_role", "get_only", "trailing_text"):
            record, expected, requests = self.world()
            if mode == "missing": requests = []
            elif mode == "probe_only": requests = requests[:1]
            elif mode == "wrong_prompt": requests[-1]["request"]["messages"][-1]["content"] = "Different task"
            elif mode == "wrong_role": requests[-1]["request"]["messages"][-1]["role"] = "system"
            elif mode == "get_only": requests[-1]["method"] = "GET"
            else: requests[-1]["request"]["messages"][-1]["content"] += "\nAdditional instruction"
            with self.subTest(mode=mode):
                self.assertFalse(self.validate(record, expected, requests)["verified"])

    def test_failure_records_still_bind_all_materials_and_actual_provider_request(self):
        for mode in ("malformed", "http_failure"):
            record, expected, requests = self.world()
            record["metrics"] = [{**metric, "verdict": "failed"} for metric in record["metrics"]]
            record["verdict"] = "failed"
            record["execution"].update(status="failed", code=mode, msg="Actual Judge request failed")
            requests[-1]["status"] = 503 if mode == "http_failure" else 200
            requests[-1]["response"] = ({"error": {"message": "Actual upstream failure"}}
                if mode == "http_failure" else {"role": "assistant", "content": "controlled invalid judge JSON"})
            with self.subTest(mode=mode):
                self.assertTrue(self.validate(record, expected, requests, failed=True)["verified"])
                self.assertFalse(self.validate(record, expected, requests[:1], failed=True)["verified"])
                expected["a" * 32]["tool_actions"][0]["result"] = "Different original result"
                self.assertFalse(self.validate(record, expected, requests, failed=True)["verified"])

    def test_declared_failure_rejects_success_unfinished_missing_and_unknown_wire(self):
        for change in ('success', 'unfinished', 'missing-ledger', 'missing-response', 'unknown-status',
                'boolean-status', 'shutdown-pending', 'request-error', 'valid-success-after-fault'):
            record, expected, requests = self.world()
            record['metrics'] = [{**metric, 'verdict': 'failed'} for metric in record['metrics']]
            record.update(verdict='failed')
            record['execution'].update(status='failed', code='declared_failure', msg='Inert declared failure')
            row = requests[-1]
            if change == 'unfinished': row.update(completed=False, status=None)
            elif change == 'missing-ledger': requests = None
            elif change == 'missing-response': row.pop('response')
            elif change == 'unknown-status': row['status'] = 0
            elif change == 'boolean-status': row['status'] = True
            elif change == 'shutdown-pending': row['shutdown_pending'] = True
            elif change == 'request-error': row.update(error='ReadTimeout', response={'content': 'invalid JSON'})
            elif change == 'valid-success-after-fault':
                requests.insert(1, {**deepcopy(row), 'status': 503})
            with self.subTest(change=change):
                proof = self.validate(record, expected, requests, failed=True)
                self.assertFalse(proof['verified'], proof)

    def test_observed_failure_must_match_explicit_fault_mode_and_wire_authority(self):
        for mode in ('malformed', 'http_failure'):
            record, expected, requests = self.world()
            good_reply = deepcopy(requests[-1]['response'])
            record['metrics'] = [{**metric, 'verdict': 'failed'} for metric in record['metrics']]
            record.update(verdict='failed')
            record['execution'].update(status='failed', code=mode, msg='Actual failure')
            row = requests[-1]
            if mode == 'http_failure': row.update(status=503, response={'error': {'message': 'Actual HTTP fault'}})
            else: row['wire_response'] = {'choices': [{'message': {'role': 'assistant', 'content': 'invalid Judge JSON'}}]}
            with self.subTest(mode=mode):
                self.assertTrue(self.validate(record, expected, requests, failed=True, expected_failure=mode)['verified'])
                other = 'malformed' if mode == 'http_failure' else 'http_failure'
                self.assertFalse(self.validate(record, expected, requests, failed=True, expected_failure=other)['verified'])
                if mode == 'malformed':
                    row['wire_response'] = {'choices': [{'message': good_reply}]}
                    row['response'] = {'role': 'assistant', 'content': 'invalid cached Judge JSON'}
                    self.assertFalse(self.validate(record, expected, requests, failed=True, expected_failure=mode)['verified'])

    def test_success_requires_a_completed_http_200_judge_attempt(self):
        for field, value in (("status", 503), ("status", True), ("completed", False),
                ("completed", 1), ("error", "ReadTimeout"), ("shutdown_pending", True)):
            record, expected, requests = self.world()
            requests[-1][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(self.validate(record, expected, requests)["verified"])

    def test_persisted_metrics_must_equal_the_actual_judge_reply(self):
        for field, value in (("name", "unrequested"), ("verdict", "bad"),
                ("reason", "A different actual assessment"), ("evidence", {"invented": True})):
            record, expected, requests = self.world()
            reply = json.loads(requests[-1]["response"]["content"])
            reply["metrics"][0][field] = value
            requests[-1]["response"]["content"] = json.dumps(reply)
            with self.subTest(field=field):
                self.assertFalse(self.validate(record, expected, requests)["verified"])

    def test_missing_malformed_or_incomplete_actual_reply_cannot_prove_success(self):
        for content in (None, "controlled invalid judge JSON", '{"metrics":[]}',
                '{"metrics":[{"name":"task_completion","verdict":"good","reason":"only one"}]}'):
            record, expected, requests = self.world()
            requests[-1]["response"]["content"] = content
            with self.subTest(content=content):
                self.assertFalse(self.validate(record, expected, requests)["verified"])

    def test_failed_retry_then_matching_success_proves_the_record(self):
        record, expected, requests = self.world()
        failed = deepcopy(requests[-1])
        failed.update(status=503, error="HTTP 503")
        requests.insert(1, failed)
        self.assertTrue(self.validate(record, expected, requests)["verified"])
        requests[-1]["response"]["content"] = "not JSON"
        self.assertFalse(self.validate(record, expected, requests)["verified"])

    def test_production_reply_parsing_preserves_reason_and_ignores_unknown_fields(self):
        record, expected, requests = self.world()
        record["metrics"][0]["reason"] = "  Original reason\n"
        reply = deepcopy(record["metrics"])
        reply[0]["name"] = " task_completion "
        reply[0]["unknown"] = "ignored by production"
        reply[1].pop("evidence")
        content = "<think>{\"metrics\":[]}</think>\n```json\n" + json.dumps({"metrics": reply[::-1]}) + "\n```"
        for shape in (content, [content], [{"type": "text", "text": content}],
                [{"type": "text", "text": {"value": content}}]):
            requests[-1]["response"]["content"] = shape
            with self.subTest(shape=type(shape).__name__):
                self.assertTrue(self.validate(record, expected, requests)["verified"])
        # The client strips names, but it keeps the reason's original whitespace.
        reply[0]["reason"] = record["metrics"][0]["reason"].strip()
        requests[-1]["response"]["content"] = json.dumps({"metrics": reply})
        self.assertFalse(self.validate(record, expected, requests)["verified"])

    def test_fake_wire_envelope_is_the_response_authority(self):
        record, expected, requests = self.world()
        wire = {"choices": [{"message": deepcopy(requests[-1]["response"])}]}
        requests[-1]["wire_response"] = wire
        self.assertTrue(self.validate(record, expected, requests)["verified"])
        wire["choices"][0]["message"]["content"] = "invalid actual wire reply"
        self.assertFalse(self.validate(record, expected, requests)["verified"])


if __name__ == "__main__":
    unittest.main()

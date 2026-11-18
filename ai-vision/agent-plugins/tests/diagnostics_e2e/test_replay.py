"""Actual OTLP encoding/admission contracts with public in-process transports."""

import base64
import inspect
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from core_trial import source_rows
from corpus import export_case, load_package, stable_hash
from replay import replay_package, replay_payload
from test_corpus import labels, provenance, review


class ReplayEntrypointTests(unittest.TestCase):
    def run_entry(self, me, *, owner_change=None, rejected=False):
        import replay

        target = {
            "endpoint": "http://127.0.0.1:8001",
            "collector_endpoint": "http://127.0.0.1:4319",
            "space_id": "owned-space",
            "agent_uuid": "owned-agent",
            "agent_product": "Codex",
            "space_key": "public-fixture",
            "data_backend": "oceanbase",
            "collector_headers": {"x-space-key": "public-fixture"},
            "dashboard_headers": {"Cookie": "original"},
            "authenticated_user_id": "stale-target-user",
        }
        original = deepcopy(target)
        calls, clients, events = [], [], []
        test = self

        class Client:
            def __init__(self, endpoint, *, target, dashboard_sessions):
                test.assertEqual(endpoint, target["endpoint"])
                test.assertIsNotNone(dashboard_sessions)
                self.target = deepcopy(target)
                self.closed = False
                clients.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.closed = True

            def request(self, method, path, *, allowed):
                test.assertEqual(
                    (method, path, allowed), ("GET", "/api/v1/auth/me", (200,))
                )
                events.append("me")
                self.target["dashboard_headers"]["Cookie"] = "rotated"
                self.target.update(owner_change or {})
                if isinstance(me, Exception):
                    raise me
                return deepcopy(me)

        def invoke(*args, **kwargs):
            events.append("replay")
            test.assertIs(kwargs["platform"], clients[0])
            calls.append((args, kwargs))
            return {"verified": True}

        with (
            patch("replay._targets", return_value={"codex": target}),
            patch.dict(
                sys.modules,
                {
                    "run_suite": SimpleNamespace(
                        native_sessions=SimpleNamespace(
                            DashboardSessions=object,
                            refresh_ingest=lambda target: target,
                        )
                    )
                },
            ),
            patch("platform_client.PlatformClient", Client),
            patch("replay.replay_package", invoke),
            redirect_stderr(io.StringIO()),
        ):
            argv = [
                "--corpus",
                "/tmp/inert-corpus",
                "--targets",
                "/tmp/inert-targets",
                "--app",
                "codex",
                "--out",
                "/tmp/inert-output",
            ]
            if rejected:
                with self.assertRaises(SystemExit) as error:
                    replay.main(argv)
                self.assertEqual(error.exception.code, 1)
            elif isinstance(me, Exception):
                with self.assertRaises(type(me)):
                    replay.main(argv)
            else:
                self.assertEqual(replay.main(argv), 0)
        self.assertEqual(target, original)
        self.assertTrue(clients[0].closed)
        return calls, clients, events

    def test_cli_reads_me_in_current_session_and_passes_explicit_scope(self):
        for user in ("native-account", "x" * 36):
            with self.subTest(user=user):
                calls, clients, events = self.run_entry({"user": {"user_uuid": user}})
                self.assertEqual(events, ["me", "replay"])
                self.assertEqual(
                    calls[0][1]["authenticated_scope"],
                    {
                        "space_id": "owned-space",
                        "agent_uuid": "owned-agent",
                        "agent_product": "Codex",
                        "authenticated_user_id": user,
                    },
                )
                self.assertEqual(
                    clients[0].target["dashboard_headers"]["Cookie"], "rotated"
                )

    def test_cli_rejects_invalid_me_without_replay_or_stale_user_fallback(self):
        invalid = [
            None,
            [],
            {},
            {"user": None},
            {"user": []},
            {"user": {}},
            {"data": {"user": {"user_uuid": "wrapped"}}},
        ]
        invalid += [
            {"user": {"user_uuid": user}}
            for user in (None, True, 1, "", " ", " account", "account ", "x" * 37)
        ]
        for me in invalid:
            with self.subTest(me=me):
                calls, _, events = self.run_entry(me, rejected=True)
                self.assertEqual(calls, [])
                self.assertEqual(events, ["me"])

    def test_cli_rejects_me_failure_and_current_owner_drift(self):
        calls, _, events = self.run_entry(RuntimeError("HTTP 401"))
        self.assertEqual((calls, events), ([], ["me"]))
        for field in ("space_id", "agent_uuid", "agent_product"):
            with self.subTest(field=field):
                calls, _, events = self.run_entry(
                    {"user": {"user_uuid": "native-account"}},
                    owner_change={field: "foreign-owner"},
                    rejected=True,
                )
                self.assertEqual((calls, events), ([], ["me"]))


def replay_source():
    from capture import decode_export
    from scenarios import scenarios

    prompt = next(item.prompt for item in scenarios() if item.name == "read-only")

    payload = {
        "resourceSpans": [
            {
                "resource": {},
                "scopeSpans": [
                    {
                        "scope": {},
                        "spans": [
                            {
                                "traceId": "a" * 32,
                                "spanId": "1" * 16,
                                "name": "Turn 1",
                                "startTimeUnixNano": "10",
                                "endTimeUnixNano": "20",
                                "attributes": [
                                    {
                                        "key": "gen_ai.span.kind",
                                        "value": {"stringValue": "CHAT"},
                                    },
                                    {
                                        "key": "gen_ai.input.messages",
                                        "value": {
                                            "stringValue": json.dumps(
                                                [{"role": "user", "content": prompt}]
                                            )
                                        },
                                    },
                                    {
                                        "key": "gen_ai.output.messages",
                                        "value": {
                                            "stringValue": '{"role":"assistant","content":"public answer"}'
                                        },
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    for identity, name, arguments, result in (
        (
            "2" * 16,
            "edit",
            {
                "file_path": "app.py",
                "old_string": "return a - b",
                "new_string": "return a + b",
                "confirmed": True,
            },
            {"ok": True, "count": 0},
        ),
        (
            "3" * 16,
            "Bash",
            {"command": "pytest -q"},
            {"exit_code": 1, "stdout": "1 failed"},
        ),
    ):
        attributes = {
            "gen_ai.span.kind": "TOOL",
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
            "gen_ai.tool.call.id": "call-" + identity,
            "gen_ai.tool.call.arguments": json.dumps(arguments),
            "gen_ai.tool.call.result": json.dumps(result),
        }
        payload["resourceSpans"][0]["scopeSpans"][0]["spans"].append(
            {
                "traceId": "a" * 32,
                "spanId": identity,
                "parentSpanId": "1" * 16,
                "name": "tool " + name,
                "startTimeUnixNano": "11",
                "endTimeUnixNano": "15",
                "attributes": [
                    {"key": key, "value": {"stringValue": value}}
                    for key, value in attributes.items()
                ],
            }
        )
    exported = decode_export("traces", json.dumps(payload).encode(), "application/json")
    exported["admitted"] = True
    return {
        "app": "codex",
        "scenario": "read-only",
        "mode": "nvidia",
        "model": "protocol-fixture",
        "exports": [exported],
        "requests": [],
        "result": {
            "details": {
                "native_transcript": [
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "last_agent_message": "public answer",
                        },
                    }
                ]
            }
        },
    }


def original_projection(source):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "agentlens/src"))
    from agentlens.service.datasource.projection.projectors.coding.adapter import (
        project_coding_raw,
    )
    from agentlens.service.datasource.projection.serialization import (
        serialize_projection,
    )

    rows = source_rows(source)
    view = serialize_projection(project_coding_raw(rows, "a" * 32)[0])
    return {**view, "trace_id": "a" * 32, "runtime_type": "codex", "spans": rows}


def public_replay(source, actual, *, authenticated_scope=None, on_ingest=None):
    class Platform:
        def scoped(self, *_args, **_kwargs):
            return {"test_input": actual}

    truth = labels()
    truth["labels"][0]["references"] = [{"trace_id": "a" * 32, "span_id": "1" * 16}]
    policy = provenance()
    policy["runtime"] = "codex"

    def ingest(request):
        if on_ingest is not None:
            on_ingest(request)
        return httpx.Response(200, json={})

    with (
        tempfile.TemporaryDirectory() as directory,
        httpx.Client(transport=httpx.MockTransport(ingest)) as client,
    ):
        root = Path(directory)
        (root / "source.json").write_text(json.dumps(source))
        export_case(
            root / "source.json",
            labels=truth,
            provenance=policy,
            review=review(),
            out=root / "package",
        )
        options = (
            {"authenticated_scope": authenticated_scope}
            if authenticated_scope is not None
            else {}
        )
        original_bytes = (root / "source.json").read_bytes()
        result = replay_package(
            root / "package/manifest.json",
            target={
                "collector_endpoint": "http://127.0.0.1:1000",
                "collector_headers": {},
            },
            platform=Platform(),
            out=root / "result",
            transport=client,
            readback_timeout=0,
            **options,
        )
        if (root / "source.json").read_bytes() != original_bytes:
            raise AssertionError("Replay changed its original source file")
        return result


def authenticated_source_and_projection():
    source = replay_source()
    exported = source["exports"][0]
    for span in exported["spans"]:
        span["attributes"].update(
            {"user.id": "native-user", "ant.username": "native-user"}
        )
    for span in exported["otlp"]["resourceSpans"][0]["scopeSpans"][0]["spans"]:
        span["attributes"].extend(
            {"key": key, "value": {"stringValue": "native-user"}}
            for key in ("user.id", "ant.username")
        )
    scope = {
        "space_id": "public-space",
        "agent_uuid": "public-agent",
        "agent_product": "AntCDX",
        "authenticated_user_id": "01234567-89ab-4cde-8fab-0123456789ab",
    }
    actual = original_projection(source)
    for row in actual["spans"]:
        row.pop("attributes.user.id")
        for key, field in (
            ("aivision.space.id", "space_id"),
            ("aivision.agent.id", "agent_uuid"),
            ("aivision.agent.product", "agent_product"),
            ("aivision.user.id", "authenticated_user_id"),
        ):
            row["resource.attributes." + key] = scope[field]
    return source, actual, scope


def cross_batch_source():
    """A public OTLP tree whose tool parents live in another export."""
    from capture import decode_export

    source = replay_source()
    payload = deepcopy(source["exports"][0]["otlp"])
    group = payload["resourceSpans"][0]
    group["resource"] = {
        "attributes": [
            {"key": "service.name", "value": {"stringValue": "public-fixture"}},
            {"key": "typed.resource", "value": {"boolValue": False}},
        ]
    }
    scoped = group["scopeSpans"][0]
    scoped["scope"] = {"name": "public-scope", "version": "1.0"}
    for span in scoped["spans"]:
        span["startTimeUnixNano"] = "1700000000000000011"
        span["endTimeUnixNano"] = "1700000000000000015"
        span["status"] = {"code": 1, "message": "public"}
        span["events"] = [
            {
                "name": "public-event",
                "attributes": [{"key": "typed.event", "value": {"boolValue": True}}],
            }
        ]
        span["attributes"].append({"key": "typed.io", "value": {"boolValue": True}})
    parent, children = deepcopy(payload), deepcopy(payload)
    parent["resourceSpans"][0]["scopeSpans"][0]["spans"] = scoped["spans"][:1]
    children["resourceSpans"] = []
    for span in scoped["spans"][1:]:
        child_group = deepcopy(group)
        child_group["scopeSpans"][0]["spans"] = [span]
        children["resourceSpans"].append(child_group)
    source["exports"] = []
    for batch in (parent, children):
        export = decode_export("traces", json.dumps(batch).encode(), "application/json")
        source["exports"].append({**export, "admitted": True})
    # Captured Source files are JSON; round-trip to keep decoded metadata and
    # OTLP dictionaries independent, as they are after a real source read.
    return json.loads(json.dumps(source))


class ReplayBatchTests(unittest.TestCase):
    def test_cross_export_parent_is_valid_for_batch_without_changing_complete_tree_gate(
        self,
    ):
        source = cross_batch_source()
        self.assertEqual(len(source_rows(source)), 3)
        child = source["exports"][1]
        with self.assertRaisesRegex(ValueError, "Source parent is absent"):
            source_rows({"exports": [child]})
        original_bytes = json.dumps(source).encode()
        self.assertEqual(replay_payload(child), child["otlp"])
        self.assertEqual(json.dumps(source).encode(), original_bytes)

    def test_cross_batch_package_keeps_original_tree_bytes_and_observed_projection(
        self,
    ):
        source = cross_batch_source()
        original_bytes = json.dumps(source).encode()
        writes = []
        report = public_replay(
            source, original_projection(source), on_ingest=writes.append
        )
        self.assertTrue(report["verified"])
        self.assertTrue(report["cases"][0]["source_diff"]["verified"])
        self.assertEqual(
            [json.loads(request.content) for request in writes],
            [export["otlp"] for export in source["exports"]],
        )
        self.assertEqual(json.dumps(source).encode(), original_bytes)

    def test_base64_cross_batch_ids_convert_to_exact_hex_without_rewriting_source(self):
        source = cross_batch_source()
        child = source["exports"][1]
        expected = deepcopy(child["otlp"])
        for group in child["otlp"]["resourceSpans"]:
            for span in group["scopeSpans"][0]["spans"]:
                for field in ("traceId", "spanId", "parentSpanId"):
                    span[field] = base64.b64encode(bytes.fromhex(span[field])).decode()
        original_bytes = json.dumps(child).encode()
        self.assertEqual(replay_payload(child), expected)
        self.assertEqual(json.dumps(child).encode(), original_bytes)

    def test_batch_rejects_invalid_and_duplicate_span_identity_or_parent_hex(self):
        for field in ("trace_id", "span_id", "parent_span_id"):
            for value in (
                True,
                123,
                "not-hex",
                "F" * (32 if field == "trace_id" else 16),
            ):
                with self.subTest(field=field, value=value):
                    export = cross_batch_source()["exports"][1]
                    export["spans"][0][field] = value
                    with self.assertRaises(ValueError):
                        replay_payload(export)
        for value in (None, [], [None]):
            with self.subTest(spans=value):
                export = cross_batch_source()["exports"][1]
                export["spans"] = value
                with self.assertRaises(ValueError):
                    replay_payload(export)
        export = cross_batch_source()["exports"][1]
        export["spans"].append(deepcopy(export["spans"][0]))
        with self.assertRaises(ValueError):
            replay_payload(export)

    def test_closed_batch_cycle_and_self_parent_remain_invalid(self):
        for self_parent in (True, False):
            with self.subTest(self_parent=self_parent):
                export = cross_batch_source()["exports"][1]
                spans = export["spans"]
                spans[0]["parent_span_id"] = spans[0 if self_parent else 1]["span_id"]
                if not self_parent:
                    spans[1]["parent_span_id"] = spans[0]["span_id"]
                for group, span in zip(export["otlp"]["resourceSpans"], spans):
                    group["scopeSpans"][0]["spans"][0]["parentSpanId"] = span[
                        "parent_span_id"
                    ]
                with self.assertRaises(ValueError):
                    replay_payload(export)

    def test_complete_report_still_rejects_missing_parent_cross_batch_cycle_or_multi_trace(
        self,
    ):
        for change in ("missing_parent", "cross_batch_cycle", "multi_trace"):
            with self.subTest(change=change):
                source = cross_batch_source()
                root, child = (
                    source["exports"][0]["spans"][0],
                    source["exports"][1]["spans"][0],
                )
                if change == "missing_parent":
                    child["parent_span_id"] = "f" * 16
                elif change == "cross_batch_cycle":
                    root["parent_span_id"] = child["span_id"]
                else:
                    child["trace_id"] = "b" * 32
                with self.assertRaises(ValueError):
                    source_rows(source)

    def test_original_nested_metadata_and_ns_clock_mismatch_are_rejected(self):
        corruptions = {
            "resource": lambda group, span: group["resource"]["attributes"][0][
                "value"
            ].update(stringValue="changed"),
            "resource_type": lambda group, span: group["resource"]["attributes"][
                1
            ].update(value={"intValue": "0"}),
            "scope_name": lambda group, span: group["scopeSpans"][0]["scope"].update(
                name="changed"
            ),
            "scope_version": lambda group, span: group["scopeSpans"][0]["scope"].update(
                version="2.0"
            ),
            "events": lambda group, span: span["events"][0].update(name="changed"),
            "event_type": lambda group, span: span["events"][0]["attributes"][0].update(
                value={"intValue": "1"}
            ),
            "status": lambda group, span: span["status"].update(message="changed"),
            "status_type": lambda group, span: span["status"].update(code=True),
            "input": lambda group, span: span["attributes"][4]["value"].update(
                stringValue="changed arguments"
            ),
            "typed_io": lambda group, span: span["attributes"][-1].update(
                value={"intValue": "1"}
            ),
            "one_ns": lambda group, span: span.update(
                startTimeUnixNano="1700000000000000012"
            ),
            "parent": lambda group, span: span.update(parentSpanId="f" * 16),
            "missing_span": lambda group, span: group["scopeSpans"][0]["spans"].clear(),
        }
        for field, corrupt in corruptions.items():
            with self.subTest(field=field):
                export = cross_batch_source()["exports"][1]
                group = export["otlp"]["resourceSpans"][0]
                span = group["scopeSpans"][0]["spans"][0]
                corrupt(group, span)
                with self.assertRaises(ValueError):
                    replay_payload(export)

    def test_complete_batch_cannot_hide_scope_event_or_status_metadata_changes(self):
        from capture import decode_export

        corruptions = {
            "scope": lambda group, span: group["scopeSpans"][0]["scope"].update(
                version="changed"
            ),
            "event": lambda group, span: span["events"][0].update(name="changed"),
            "event_type": lambda group, span: span["events"][0]["attributes"][0].update(
                value={"intValue": "1"}
            ),
            "status": lambda group, span: span["status"].update(message="changed"),
        }
        for field, corrupt in corruptions.items():
            with self.subTest(field=field):
                source = cross_batch_source()
                payload = {
                    "resourceSpans": [
                        group
                        for export in source["exports"]
                        for group in export["otlp"]["resourceSpans"]
                    ]
                }
                export = decode_export(
                    "traces", json.dumps(payload).encode(), "application/json"
                )
                export["admitted"] = True
                export = json.loads(json.dumps(export))
                self.assertEqual(len(source_rows({"exports": [export]})), 3)
                self.assertEqual(replay_payload(export), payload)
                group = export["otlp"]["resourceSpans"][0]
                corrupt(group, group["scopeSpans"][0]["spans"][0])
                with self.assertRaises(ValueError):
                    replay_payload(export)


class ReplayTests(unittest.TestCase):
    def test_authenticated_replay_records_raw_failure_and_semantic_gate_without_mutating_wire(
        self,
    ):
        self.assertIn(
            "authenticated_scope", inspect.signature(replay_package).parameters
        )
        source, actual, scope = authenticated_source_and_projection()
        original_bytes = json.dumps(source).encode()
        writes = []
        report = public_replay(
            source, actual, authenticated_scope=scope, on_ingest=writes.append
        )
        case = report["cases"][0]
        self.assertTrue(report["verified"])
        self.assertFalse(case["source_diff"]["verified"])
        self.assertTrue(case["authenticated_source_diff"]["verified"])
        self.assertEqual(
            case["authenticated_source_diff"]["strict_original_rows"],
            case["source_diff"],
        )
        self.assertEqual(
            len(case["authenticated_source_diff"]["identity_transforms"]), 3
        )
        self.assertTrue(case["projection_diff"]["verified"])
        self.assertEqual(json.dumps(source).encode(), original_bytes)
        self.assertEqual(json.loads(writes[0].content), source["exports"][0]["otlp"])
        for span in json.loads(writes[0].content)["resourceSpans"][0]["scopeSpans"][0][
            "spans"
        ]:
            self.assertIn(
                {"key": "user.id", "value": {"stringValue": "native-user"}},
                span["attributes"],
            )

    def test_default_replay_still_rejects_user_strip_with_successful_projection(self):
        source, actual, _scope = authenticated_source_and_projection()
        report = public_replay(source, actual)
        self.assertFalse(report["verified"])
        self.assertFalse(report["cases"][0]["source_diff"]["verified"])
        self.assertTrue(report["cases"][0]["projection_diff"]["verified"])
        self.assertNotIn("authenticated_source_diff", report["cases"][0])

    def test_authenticated_replay_cannot_accept_wrong_scope_or_unproved_alias(self):
        self.assertIn(
            "authenticated_scope", inspect.signature(replay_package).parameters
        )
        source, actual, scope = authenticated_source_and_projection()
        for field in scope:
            with self.subTest(field=field):
                report = public_replay(
                    source, actual, authenticated_scope={**scope, field: "foreign"}
                )
                self.assertFalse(report["verified"])
                self.assertFalse(
                    report["cases"][0]["authenticated_source_diff"]["verified"]
                )
        changed = deepcopy(actual)
        changed["spans"][0].pop("attributes.ant.username")
        report = public_replay(source, changed, authenticated_scope=scope)
        self.assertFalse(report["verified"])

    def test_authenticated_source_gate_does_not_hide_corrupt_projection(self):
        self.assertIn(
            "authenticated_scope", inspect.signature(replay_package).parameters
        )
        source, actual, scope = authenticated_source_and_projection()
        actual["agent_output"] = "invented final answer"
        report = public_replay(source, actual, authenticated_scope=scope)
        self.assertTrue(report["cases"][0]["authenticated_source_diff"]["verified"])
        self.assertFalse(report["cases"][0]["projection_diff"]["verified"])
        self.assertFalse(report["verified"])

    def test_authenticated_readback_waits_for_scope_and_alias_gate(self):
        from replay import readback

        self.assertIn("authenticated_scope", inspect.signature(readback).parameters)
        source, actual, scope = authenticated_source_and_projection()
        wrong_scope = deepcopy(actual)
        wrong_scope["spans"][0]["resource.attributes.aivision.user.id"] = "foreign"
        values = [wrong_scope, actual]

        class Platform:
            def scoped(self, *_args, **_kwargs):
                return {"test_input": values.pop(0)}

        with patch("replay.time.sleep"):
            observed = readback(
                Platform(), source_rows(source), authenticated_scope=scope
            )
        self.assertEqual(observed, actual)
        self.assertFalse(values)

    def test_same_final_cannot_hide_corrupt_original_projection_material(self):
        source = replay_source()
        actual = original_projection(source)
        self.assertTrue(actual["file_mutations"])
        self.assertTrue(actual["verification_actions"])
        self.assertTrue(public_replay(source, actual)["verified"])
        corruptions = {
            "query": lambda view: view.update(user_query="invented question"),
            "final_source": lambda view: view.update(agent_output_span_id="2" * 16),
            "call_id": lambda view: view["tool_actions"][0].update(call_id="invented"),
            "typed_arguments": lambda view: view["tool_actions"][0]["arguments"].update(
                confirmed=1
            ),
            "typed_result": lambda view: view["tool_actions"][0]["result"].update(ok=1),
            "missing_tool": lambda view: view["tool_actions"].pop(),
            "mutation": lambda view: view["file_mutations"][0].update(
                after="invented repair"
            ),
            "missing_mutation": lambda view: view.update(file_mutations=[]),
            "verification": lambda view: view["verification_actions"][0].update(
                result="passed"
            ),
            "missing_verification": lambda view: view.update(verification_actions=[]),
        }
        for field, corrupt in corruptions.items():
            changed = deepcopy(actual)
            corrupt(changed)
            with self.subTest(field=field):
                self.assertEqual(changed["agent_output"], actual["agent_output"])
                self.assertFalse(public_replay(source, changed)["verified"])

    def test_readback_waits_for_complete_inventory_without_accepting_permanent_loss(
        self,
    ):
        from replay import readback

        rows = source_rows(replay_source())
        absent = RuntimeError("Public pending ingestion")
        absent.http_status = 404
        values = [
            absent,
            {"test_input": {"trace_id": "a" * 32, "spans": []}},
            {"test_input": {"trace_id": "a" * 32, "spans": rows}},
        ]

        class Platform:
            def scoped(self, *_args, **_kwargs):
                value = values.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value

        with patch("replay.time.sleep"):
            sample = readback(Platform(), rows)
        self.assertEqual(sample["spans"], rows)
        self.assertFalse(values)

    def test_ingest_uses_exact_original_ids_times_then_reads_complete_platform_source(
        self,
    ):
        source = replay_source()
        truth = labels()
        truth["labels"][0]["references"] = [{"trace_id": "a" * 32, "span_id": "1" * 16}]
        policy = provenance()
        policy["runtime"] = "codex"
        target = {
            "collector_endpoint": "http://127.0.0.1:1000",
            "collector_headers": {},
            "agent_uuid": "public-agent",
        }
        writes = []

        def ingest(request):
            writes.append(json.loads(request.content))
            return httpx.Response(200, json={})

        class Platform:
            def scoped(self, suffix, **kwargs):
                self.params = kwargs["params"]
                return {"test_input": original_projection(source)}

        platform = Platform()
        client = httpx.Client(transport=httpx.MockTransport(ingest))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.json").write_text(json.dumps(source))
            export_case(
                root / "source.json",
                labels=truth,
                provenance=policy,
                review=review(),
                out=root / "package",
            )
            original = load_package(root / "package/manifest.json")
            result = replay_package(
                root / "package/manifest.json",
                target=target,
                platform=platform,
                out=root / "result",
                transport=client,
            )
            self.assertTrue(result["verified"])
            self.assertEqual(writes[0], source["exports"][0]["otlp"])
            self.assertEqual(platform.params["trace_id"], "a" * 32)
            self.assertEqual(original, load_package(root / "package/manifest.json"))
            self.assertEqual(result["cases"][0]["source_sha256"], stable_hash(source))
        client.close()

    def test_rejection_and_truncated_readback_remain_failure(self):
        source = replay_source()
        truth = labels()
        truth["labels"][0]["references"] = [{"trace_id": "a" * 32, "span_id": "1" * 16}]
        for rejected in (True, False):
            with (
                self.subTest(rejected=rejected),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "source.json").write_text(json.dumps(source))
                export_case(
                    root / "source.json",
                    labels=truth,
                    provenance=provenance(),
                    review=review(),
                    out=root / "package",
                )

                class Platform:
                    def scoped(self, *_args, **_kwargs):
                        return {
                            "test_input": {
                                "trace_id": "a" * 32,
                                "runtime_type": "codex",
                                "projection_kind": "semantic",
                                "agent_output": "public answer",
                                "spans": [],
                            }
                        }

                response = (
                    {"partialSuccess": {"rejectedSpans": "1"}} if rejected else {}
                )
                with httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _, value=response: httpx.Response(200, json=value)
                    )
                ) as client:
                    result = replay_package(
                        root / "package/manifest.json",
                        target={
                            "collector_endpoint": "http://127.0.0.1:1000",
                            "collector_headers": {},
                        },
                        platform=Platform(),
                        out=root / "result",
                        transport=client,
                        readback_timeout=0,
                    )
                self.assertFalse(result["verified"])

    def test_payload_codec_rejects_identity_mismatch_without_mutating_original(self):
        source = replay_source()
        original = deepcopy(source)
        payload = replay_payload(source["exports"][0])
        self.assertEqual(source, original)
        self.assertEqual(payload, source["exports"][0]["otlp"])
        source["exports"][0]["otlp"]["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
            "traceId"
        ] = "b" * 32
        with self.assertRaises(ValueError):
            replay_payload(source["exports"][0])

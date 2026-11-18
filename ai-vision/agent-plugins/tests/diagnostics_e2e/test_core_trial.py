"""Public protocol controls; no semantic Judge accuracy claim."""

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import core_trial
from calibrate import AttemptBudget, CalibrationError, CredentialLease
from core_trial import CoreTrialExecutor, compare_rows, source_rows, transport_timeout
from corpus import stable_hash


def evidence():
    return {
        "app": "codex",
        "exports": [
            {
                "admitted": True,
                "signal": "traces",
                "spans": [
                    {
                        "trace_id": "a" * 32,
                        "span_id": "1" * 16,
                        "parent_span_id": "",
                        "name": "Turn 1",
                        "resource": {"service.name": "public", "flag": False},
                        "start_time_unix_nano": 10,
                        "end_time_unix_nano": 20,
                        "attributes": {
                            "gen_ai.input.messages": '[{"role":"user","content":"hello"}]',
                            "null": None,
                            "zero": 0,
                            "boolean": True,
                            "typed.io": {"ok": True, "count": 0, "missing": None},
                        },
                    }
                ],
            }
        ],
    }


def template():
    definition = {
        "key": "task_completion",
        "variant": "text_response",
        "version": "1.0.0",
        "mode": "llm",
        "name": "Public",
        "description": "Public",
        "spec": {"prompt": "Public prompt"},
    }
    return {
        "identity": "task_completion/text_response",
        "revision": "1.0.0",
        "version": "1.0.0",
        "variant": "text_response",
        "prompt": "Public prompt",
        "definition_digest": stable_hash(definition),
        "definition": definition,
        "core": True,
    }


class TrialTests(unittest.TestCase):
    def test_mapping_retains_original_fields_and_rejects_truncated_typed_readback(self):
        original = evidence()
        rows = source_rows(original)
        self.assertIsNone(rows[0]["attributes.null"])
        self.assertEqual(rows[0]["attributes.zero"], "0")
        self.assertEqual(rows[0]["attributes.boolean"], "true")
        self.assertEqual(
            rows[0]["attributes.typed.io"], '{"ok":true,"count":0,"missing":null}'
        )
        self.assertEqual(rows[0]["resource.attributes.service.name"], "public")
        self.assertEqual(rows[0]["resource.attributes.flag"], "false")
        self.assertEqual(
            rows[0]["attributes.gen_ai.input.messages"],
            original["exports"][0]["spans"][0]["attributes"]["gen_ai.input.messages"],
        )
        self.assertEqual(rows[0]["start_time_unix_nano"], 10)
        self.assertEqual(original, evidence())
        self.assertTrue(compare_rows(rows, rows)["verified"])
        changed = deepcopy(rows)
        changed[0]["attributes.boolean"] = 1
        self.assertFalse(compare_rows(rows, changed)["verified"])
        self.assertFalse(compare_rows(rows, [])["verified"])

    def test_catalog_checked_every_call_and_drift_prevents_post(self):
        value = template()
        calls = []
        provider = type(
            "Provider", (), {"requests": [], "url": "http://127.0.0.1:1000/v1"}
        )()

        class Client:
            base = "/api/v1/spaces/public/diagnose"

            def __init__(self):
                self.target = {"agent_uuid": "public-agent"}

            def request(self, method, path, **kwargs):
                calls.append((method, path, kwargs))
                if method == "GET":
                    return {
                        "templates": [
                            {
                                "key": "task_completion",
                                "variant": value["variant"],
                                "version": value["version"],
                                "definition_digest": value["definition_digest"],
                            }
                        ]
                    }
                scope.reserve()
                provider.requests.append(
                    {"attempt_number": scope.identities[-1], "usage": {"tokens": 0}}
                )
                return {
                    "skipped": False,
                    "verdict": "good",
                    "metrics": [{"name": "task_completion", "verdict": "good"}],
                    "execution": {"status": "succeeded"},
                }

        with (
            tempfile.TemporaryDirectory() as directory,
            CredentialLease(
                hashlib.sha256(b"public").hexdigest(), Path(directory) / "locks"
            ) as lease,
        ):
            scope = AttemptBudget(lease)
            context = {
                "budget": scope,
                "provider_url": "http://127.0.0.1:1000/v1",
                "frozen": {
                    "parameters": {"request_timeout_sec": 300},
                    "templates": {"task_completion": deepcopy(value)},
                },
            }
            executor = CoreTrialExecutor(
                Client(), connection_uuid="public-model", provider=provider
            )
            with scope.case():
                result = executor(evidence(), "task_completion", context=context)
            self.assertEqual(result["predicted"], "good")
            self.assertEqual(result["diagnosis"]["metrics"][0]["verdict"], "good")
            self.assertEqual(result["attempt_ledger"][0]["usage"], {"tokens": 0})
            post = calls[-1][2]
            self.assertGreaterEqual(post["timeout"], 3 * 300 + 3)
            self.assertEqual(
                post["body"]["spec"]["model_connection_uuid"], "public-model"
            )
            self.assertNotIn("prompt", post["body"]["spec"])
            self.assertIn("spans", post["body"]["test_input"])
            value["definition_digest"] = "c" * 64
            with scope.case(), self.assertRaises(CalibrationError):
                executor(evidence(), "task_completion", context=context)
            self.assertEqual(sum(call[0] == "POST" for call in calls), 1)

    def test_variant_identity_and_timeout_fail_closed(self):
        self.assertEqual(transport_timeout({}), 213)
        for invalid in (float("nan"), float("inf"), 0, 301, True):
            with self.subTest(invalid=invalid), self.assertRaises(CalibrationError):
                transport_timeout({"request_timeout_sec": invalid})
        # Both task_completion variants cannot share one frozen metric-key slot.
        from core_trial import core_binding

        item = template()
        item["variant"] = "executed_artifact"
        with self.assertRaises(CalibrationError):
            core_binding("task_completion", item)
        item = template()
        item["prompt"] = "Changed after freeze"
        with self.assertRaises(CalibrationError):
            core_binding("task_completion", item)

    def test_source_rejection_and_multi_trace_cannot_enter_one_trial(self):
        item = evidence()
        item["exports"][0]["admitted"] = False
        with self.assertRaises(CalibrationError):
            source_rows(item)
        item = evidence()
        item["exports"][0]["spans"][0]["parent_span_id"] = "1" * 16
        with self.assertRaises(CalibrationError):
            source_rows(item)
        item = evidence()
        other = deepcopy(item["exports"][0]["spans"][0])
        other.update(trace_id="b" * 32, span_id="2" * 16)
        item["exports"][0]["spans"].append(other)
        with self.assertRaises(CalibrationError):
            source_rows(item)


class AuthenticatedRowsTests(unittest.TestCase):
    def setUp(self):
        self.scope = {
            "space_id": "public-space",
            "agent_uuid": "public-agent",
            "agent_product": "AntCDX",
            "authenticated_user_id": "01234567-89ab-4cde-8fab-0123456789ab",
        }
        self.expected = [
            {
                "trace_id": "a" * 32,
                "span_id": "1" * 16,
                "parent_span_id": "",
                "name": "Original native turn",
                "start_time_unix_nano": 1700000000000000010,
                "end_time_unix_nano": 1700000000000000020,
                "status_code": 1,
                "attributes.user.id": "native-user",
                "attributes.ant.username": "native-user",
                "attributes.gen_ai.input.messages": '[{"role":"user","content":"hello"}]',
                "attributes.gen_ai.output.messages": '{"role":"assistant","content":"done"}',
                "attributes.typed.io": '{"ok":true,"count":0,"missing":null}',
                "resource.attributes.service.name": "public",
                "resource.attributes.ant.username": "native-user",
            },
            {
                "trace_id": "a" * 32,
                "span_id": "2" * 16,
                "parent_span_id": "1" * 16,
                "name": "Original native tool",
                "start_time_unix_nano": 1700000000000000011,
                "end_time_unix_nano": 1700000000000000015,
                "attributes.gen_ai.tool.call.result": '{"exit_code":0,"stdout":"2 tests, OK"}',
            },
        ]
        self.actual = deepcopy(self.expected)
        self.actual[0].pop("attributes.user.id")
        for row in self.actual:
            for key, field in (
                ("aivision.space.id", "space_id"),
                ("aivision.agent.id", "agent_uuid"),
                ("aivision.agent.product", "agent_product"),
                ("aivision.user.id", "authenticated_user_id"),
            ):
                row["resource.attributes." + key] = self.scope[field]

    def compare(self, expected=None, actual=None, *, scope=None):
        function = getattr(core_trial, "compare_authenticated_rows", None)
        self.assertTrue(
            callable(function), "Explicit authenticated row comparison is missing"
        )
        return function(
            self.expected if expected is None else expected,
            self.actual if actual is None else actual,
            scope=self.scope if scope is None else scope,
        )

    def test_reserved_user_strip_keeps_strict_failure_and_records_only_proven_alias(
        self,
    ):
        strict = compare_rows(self.expected, self.actual)
        self.assertEqual(
            strict, {"verified": False, "failures": ["1" * 16 + ":attributes.user.id"]}
        )
        result = self.compare()
        self.assertTrue(result["verified"])
        self.assertEqual(result["strict_original_rows"], strict)
        self.assertEqual(result["failures"], [])
        self.assertEqual(len(result["identity_transforms"]), 1)
        transform = result["identity_transforms"][0]
        self.assertEqual(transform["field"], "attributes.user.id")
        self.assertEqual(transform["trace_id"], "a" * 32)
        self.assertEqual(transform["span_id"], "1" * 16)
        self.assertIn("attributes.ant.username", transform["preserved_aliases"])

    def test_each_explicit_same_value_native_business_alias_can_prove_the_strip(self):
        for alias in ("gen_ai.user.id", "langfuse.user.id", "ant.uid", "ant.username"):
            with self.subTest(alias=alias):
                expected, actual = deepcopy(self.expected), deepcopy(self.actual)
                expected[0].pop("attributes.ant.username")
                actual[0].pop("attributes.ant.username")
                expected[0]["attributes." + alias] = "native-user"
                actual[0]["attributes." + alias] = "native-user"
                self.assertTrue(self.compare(expected, actual)["verified"])

    def test_missing_changed_different_or_resource_only_alias_cannot_prove_strip(self):
        for changed in ("missing", "different", "resource_only", "source_different"):
            with self.subTest(changed=changed):
                expected, actual = deepcopy(self.expected), deepcopy(self.actual)
                if changed in {"missing", "resource_only"}:
                    expected[0].pop("attributes.ant.username")
                    actual[0].pop("attributes.ant.username")
                elif changed == "different":
                    actual[0]["attributes.ant.username"] = "foreign-user"
                else:
                    expected[0]["attributes.ant.username"] = "different-business-user"
                    actual[0]["attributes.ant.username"] = "different-business-user"
                self.assertFalse(self.compare(expected, actual)["verified"])

    def test_reserved_user_must_be_absent_even_when_retained_value_matches(self):
        for value in ("native-user", self.scope["authenticated_user_id"], None, True):
            with self.subTest(value=value):
                actual = deepcopy(self.actual)
                actual[0]["attributes.user.id"] = value
                self.assertFalse(self.compare(actual=actual)["verified"])
        actual = deepcopy(self.actual)
        actual[1]["attributes.user.id"] = "spoofed-on-other-span"
        self.assertFalse(self.compare(actual=actual)["verified"])

    def test_all_spans_require_all_four_exact_typed_trusted_scope_values(self):
        for field in (
            "aivision.space.id",
            "aivision.agent.id",
            "aivision.agent.product",
            "aivision.user.id",
        ):
            for changed in ("missing", "foreign", "non_string"):
                with self.subTest(field=field, changed=changed):
                    actual = deepcopy(self.actual)
                    key = "resource.attributes." + field
                    if changed == "missing":
                        actual[1].pop(key)
                    else:
                        actual[1][key] = True if changed == "non_string" else "foreign"
                    self.assertFalse(self.compare(actual=actual)["verified"])

    def test_explicit_scope_requires_four_nonempty_strings_and_model_user_length(self):
        for field in self.scope:
            for value in ("", " ", True, 1, None):
                with self.subTest(field=field, value=value):
                    scope = {**self.scope, field: value}
                    self.assertFalse(self.compare(scope=scope)["verified"])
            scope = deepcopy(self.scope)
            scope.pop(field)
            self.assertFalse(self.compare(scope=scope)["verified"])
        self.assertFalse(
            self.compare(scope={**self.scope, "authenticated_user_id": "u" * 37})[
                "verified"
            ]
        )
        # Existing User.uuid is a 36-character CharField, not a format-normalizer.
        scope = {**self.scope, "authenticated_user_id": "a" * 32}
        actual = deepcopy(self.actual)
        for row in actual:
            row["resource.attributes.aivision.user.id"] = "a" * 32
        self.assertTrue(self.compare(actual=actual, scope=scope)["verified"])

    def test_strip_cannot_hide_inventory_clock_io_parent_or_type_corruption(self):
        changes = {
            "missing_span": lambda rows: rows.pop(),
            "duplicate_span": lambda rows: rows.append(deepcopy(rows[0])),
            "foreign_span": lambda rows: rows[1].update(span_id="3" * 16),
            "parent": lambda rows: rows[1].update(parent_span_id="3" * 16),
            "clock": lambda rows: rows[1].update(
                start_time_unix_nano=1700000000000000012
            ),
            "output": lambda rows: rows[0].update(
                {"attributes.gen_ai.output.messages": "changed"}
            ),
            "input": lambda rows: rows[0].update(
                {"attributes.gen_ai.input.messages": "changed"}
            ),
            "typed_result": lambda rows: rows[0].update(
                {"attributes.typed.io": {"ok": 1}}
            ),
            "tool_result": lambda rows: rows[1].update(
                {"attributes.gen_ai.tool.call.result": "2 failed"}
            ),
            "status_type": lambda rows: rows[0].update(status_code="1"),
        }
        for change, mutate in changes.items():
            with self.subTest(change=change):
                actual = deepcopy(self.actual)
                mutate(actual)
                self.assertFalse(self.compare(actual=actual)["verified"])

    def test_other_reserved_resource_fields_are_never_ignored_or_rewritten(self):
        for field in (
            "aivision.ingest.key_id",
            "aivision.org.id",
            "user.id",
            "space_id",
        ):
            with self.subTest(field=field):
                expected, actual = deepcopy(self.expected), deepcopy(self.actual)
                expected[0]["resource.attributes." + field] = "original-source-value"
                actual[0]["resource.attributes." + field] = "changed-auth-value"
                self.assertFalse(self.compare(expected, actual)["verified"])
        expected = deepcopy(self.expected)
        expected[0]["resource.attributes.aivision.agent.id"] = "source-spoofed-agent"
        self.assertFalse(self.compare(expected)["verified"])
        for key in (
            "aivision.space.id",
            "aivision.agent.id",
            "aivision.agent.product",
            "aivision.user.id",
        ):
            expected = deepcopy(self.expected)
            expected[0]["resource.attributes." + key] = "source-spoofed-identity"
            self.assertFalse(self.compare(expected)["verified"])

    def test_malformed_scope_cannot_save_a_semantic_verification_claim(self):
        for scope in (None, True, [], "public-scope", {**self.scope, "extra": "value"}):
            with self.subTest(scope=scope):
                function = getattr(core_trial, "compare_authenticated_rows", None)
                self.assertTrue(callable(function))
                result = function(self.expected, self.actual, scope=scope)
                self.assertFalse(result["verified"])
                self.assertEqual(result["identity_transforms"], [])
                self.assertFalse(result["strict_original_rows"]["verified"])

    def test_comparison_keeps_original_input_rows_and_serialized_source_bytes(self):
        original_expected, original_actual = (
            deepcopy(self.expected),
            deepcopy(self.actual),
        )
        before = json.dumps(self.expected, ensure_ascii=False).encode()
        self.assertTrue(self.compare()["verified"])
        self.assertEqual(self.expected, original_expected)
        self.assertEqual(self.actual, original_actual)
        self.assertEqual(json.dumps(self.expected, ensure_ascii=False).encode(), before)

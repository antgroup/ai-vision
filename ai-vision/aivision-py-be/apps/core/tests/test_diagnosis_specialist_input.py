"""Pure boundary tests; no database or provider."""

import unittest
from copy import deepcopy

from apps.core.services.diagnosis_specialist_input import (
    SpecialistError,
    normalize_configuration,
    normalize_sync,
    normalize_trial,
)


def numeric():
    return {
        "key": "numeric_consistency",
        "variant": "decimal_binary",
        "configuration": {
            "policy": {
                "kind": "numeric_consistency_v1",
                "checks": [
                    {
                        "id": "sum",
                        "expression": {
                            "field": "current_request",
                            "prefix": "Compute: ",
                            "suffix": ";",
                        },
                        "result": {
                            "field": "final_output",
                            "prefix": "Result: ",
                            "suffix": ".",
                        },
                        "precision": {"mode": "exact"},
                        "unit": None,
                    }
                ],
            }
        },
    }


def protected():
    return {
        "key": "protected_content_exposure",
        "variant": "configured_exact_markers",
        "configuration": {
            "policy": {
                "kind": "protected_content_exposure_v1",
                "scope": "final_visible_text",
                "match": "literal_exact",
                "markers": [
                    {
                        "id": "token",
                        "value": "private-fixture-literal",
                        "visibility": "protected",
                    }
                ],
            }
        },
    }


def required_tool():
    return {
        "key": "required_tool_execution",
        "variant": "observed_tool_completion",
        "configuration": {
            "policy": {
                "kind": "required_tool_execution_v1",
                "tool_name": "mcp__orders__lookup",
            }
        },
    }


class SpecialistInputTests(unittest.TestCase):
    def test_valid_and_authority_fields(self):
        self.assertEqual(normalize_sync(numeric()), numeric())
        for field in (
            "spec",
            "prompt",
            "dataset_spec",
            "space_uuid",
            "agent_uuid",
            "source",
            "complete",
            "spans",
        ):
            with self.subTest(field=field), self.assertRaises(SpecialistError):
                normalize_sync({**numeric(), field: True})
        value = numeric()
        value["configuration"]["policy"]["checks"][0]["expression"]["regex"] = ".*"
        with self.assertRaises(SpecialistError):
            normalize_sync(value)

    def test_rule_rejects_model(self):
        for field, value in (("model_connection_uuid", "m"), ("llm_options", {})):
            body = numeric()
            body["configuration"][field] = value
            with self.assertRaises(SpecialistError):
                normalize_sync(body)

    def test_unavailable(self):
        for key in (
            "subject_consistency",
            "provided_context_use",
            "data_request_fulfilment",
        ):
            with self.assertRaisesRegex(SpecialistError, "SPECIALIST_UNAVAILABLE"):
                normalize_sync({**numeric(), "key": key})

    def test_new_rule_policies_reject_unbounded_or_authoritative_configuration(self):
        tool = required_tool()
        structure = {
            "key": "structured_output_contract",
            "variant": "json_object_fields",
            "configuration": {
                "policy": {
                    "kind": "structured_output_contract_v1",
                    "fields": [{"name": "approved", "type": "boolean"}],
                }
            },
        }
        for body in (tool, structure):
            self.assertEqual(normalize_sync(body), body)
            trial = {"trace_id": "trace", "draft": body}
            self.assertEqual(normalize_trial(trial), trial)
            patch = {
                "expected_configuration_digest": "a" * 64,
                "configuration": body["configuration"],
            }
            self.assertEqual(normalize_configuration(patch), patch)
            with self.assertRaises(SpecialistError):
                normalize_sync({**body, "variant": "other"})
            value = deepcopy(body)
            value["configuration"]["model_connection_uuid"] = "model"
            with self.assertRaises(SpecialistError):
                normalize_sync(value)
        for update in (
            {"tool_name": "orders.*"},
            {"tool_name": "x" * 129},
            {"source_complete": True},
        ):
            value = deepcopy(tool)
            value["configuration"]["policy"].update(update)
            with self.assertRaises(SpecialistError):
                normalize_sync(value)
        for fields in (
            [],
            [{"name": "object.field", "type": "string"}],
            [{"name": "approved", "type": "float"}],
            [{"name": "approved", "type": "boolean"}] * 2,
            [{"name": f"f{i}", "type": "null"} for i in range(17)],
        ):
            value = deepcopy(structure)
            value["configuration"]["policy"]["fields"] = fields
            with self.assertRaises(SpecialistError):
                normalize_sync(value)

    def test_numeric_strict(self):
        for mutate in (
            lambda p: p["checks"][0].update(unit="m"),
            lambda p: p["checks"][0].update(
                precision={
                    "mode": "quantized",
                    "scale": True,
                    "rounding": "ROUND_HALF_UP",
                }
            ),
            lambda p: p["checks"].append(deepcopy(p["checks"][0])),
            lambda p: p["checks"][0]["expression"].update(field="final_output"),
        ):
            value = numeric()
            mutate(value["configuration"]["policy"])
            with self.assertRaises(SpecialistError):
                normalize_sync(value)
        value = numeric()
        value["variant"] = "decimal_binary_same_unit"
        value["configuration"]["policy"]["checks"][0]["unit"] = "m"
        self.assertEqual(normalize_sync(value), value)

    def test_protected_overlap_safe_error(self):
        for marker in (
            {"id": "public", "value": "private-fixture", "visibility": "public"},
            {
                "id": "other",
                "value": "private-fixture-literal",
                "visibility": "protected",
            },
        ):
            value = protected()
            value["configuration"]["policy"]["markers"].append(marker)
            with self.assertRaises(SpecialistError) as caught:
                normalize_sync(value)
            self.assertNotIn("private-fixture", str(caught.exception))
        self.assertEqual(normalize_sync(protected()), protected())

    def test_preserve_only_patch(self):
        value = {
            "expected_configuration_digest": "a" * 64,
            "configuration": {"policy": {"kind": "preserve_existing_policy"}},
        }
        self.assertEqual(normalize_configuration(value), value)
        body = protected()
        body["configuration"] = value["configuration"]
        with self.assertRaises(SpecialistError):
            normalize_sync(body)
        for digest in (True, "a" * 63, "A" * 64):
            with self.assertRaises(SpecialistError):
                normalize_configuration(
                    {**value, "expected_configuration_digest": digest}
                )

    def test_trial_xor_trace_only(self):
        for value in (
            {"trace_id": "trace:1", "diagnoser_uuid": "saved"},
            {"trace_id": "trace:1", "draft": numeric()},
        ):
            self.assertEqual(normalize_trial(value), value)
        for value in (
            {"trace_id": "t"},
            {"trace_id": "t", "diagnoser_uuid": "s", "draft": numeric()},
            {"trace_id": "t", "diagnoser_uuid": "s", "test_input": {}},
            {"trace_id": "t'; SELECT", "diagnoser_uuid": "s"},
        ):
            with self.assertRaises(SpecialistError):
                normalize_trial(value)

    def test_llm_options_and_language(self):
        template = {
            "key": "response_language",
            "variant": "configured_final_text",
            "configuration": {
                "policy": {
                    "kind": "response_language_v1",
                    "scope": "final_natural_language",
                    "required_language": "en",
                    "allowed_terms": ["API"],
                },
                "model_connection_uuid": "m",
            },
        }
        self.assertEqual(normalize_sync(template), template)
        for options in (
            {"max_tokens": True},
            {"max_tokens": 8193},
            {"temperature": float("nan")},
            {"request_timeout_sec": 301},
            {"reasoning_effort": False},
            {"endpoint": "private"},
        ):
            value = deepcopy(template)
            value["configuration"]["llm_options"] = options
            with self.assertRaises(SpecialistError):
                normalize_sync(value)
        template["configuration"]["policy"]["required_language"] = "auto"
        with self.assertRaises(SpecialistError):
            normalize_sync(template)

    def test_limits_variants(self):
        value = numeric()
        value["configuration"]["policy"]["checks"] *= 9
        with self.assertRaises(SpecialistError):
            normalize_sync(value)
        value = protected()
        value["configuration"]["policy"]["markers"][0]["value"] = "x" * 257
        with self.assertRaises(SpecialistError):
            normalize_sync(value)
        with self.assertRaises(SpecialistError):
            normalize_sync({**numeric(), "variant": "recorded_conversation"})


class SpecialistConfigurationPatchTests(unittest.TestCase):
    def test_policy_patch_preserves_omitted_model_options_without_widening_create(self):
        policies = (
            {
                "kind": "answer_relevance_v1",
                "context_mode": "explicit_current_text",
            },
            {
                "kind": "followup_reasonableness_v1",
                "context_mode": "recorded_conversation",
            },
            {
                "kind": "response_language_v1",
                "required_language": "en",
                "scope": "final_natural_language",
                "allowed_terms": [],
            },
        )
        for policy in policies:
            with self.subTest(kind=policy["kind"]):
                body = {
                    "expected_configuration_digest": "a" * 64,
                    "configuration": {"policy": policy},
                }
                self.assertEqual(normalize_configuration(body), body)
                key = policy["kind"].removesuffix("_v1")
                variant = (
                    policy.get("context_mode")
                    if key != "response_language"
                    else "configured_final_text"
                )
                draft = {
                    "key": key,
                    "variant": variant,
                    "configuration": body["configuration"],
                }
                with self.assertRaises(SpecialistError):
                    normalize_sync(draft)
                with self.assertRaises(SpecialistError):
                    normalize_trial({"trace_id": "trace:patch", "draft": draft})

    def test_policy_patch_still_validates_supplied_fields_and_requires_exact_cas(self):
        body = {
            "expected_configuration_digest": "a" * 64,
            "configuration": {
                "policy": {
                    "kind": "response_language_v1",
                    "required_language": "en",
                    "scope": "final_natural_language",
                    "allowed_terms": [],
                },
            },
        }
        for field, value in (
            ("model_connection_uuid", None),
            ("model_connection_uuid", ""),
            ("model_connection_uuid", "../../another-space"),
            ("llm_options", None),
            ("llm_options", {"request_timeout_sec": 301}),
            ("llm_options", {"endpoint": "fixture-private-endpoint"}),
        ):
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(SpecialistError),
            ):
                normalize_configuration(
                    {**body, "configuration": {**body["configuration"], field: value}}
                )
        for digest in (None, "", "A" * 64, "a" * 63):
            with self.assertRaises(SpecialistError):
                normalize_configuration(
                    {**body, "expected_configuration_digest": digest}
                )
        with self.assertRaises(SpecialistError):
            normalize_configuration({"configuration": body["configuration"]})
        rule_configuration = numeric()["configuration"]
        self.assertEqual(
            normalize_configuration({**body, "configuration": rule_configuration})[
                "configuration"
            ],
            rule_configuration,
        )
        for field, value in (("model_connection_uuid", "m"), ("llm_options", {})):
            with self.assertRaises(SpecialistError):
                normalize_configuration(
                    {**body, "configuration": {**rule_configuration, field: value}}
                )

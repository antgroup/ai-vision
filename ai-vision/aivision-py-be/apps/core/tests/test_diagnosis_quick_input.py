import unittest

from apps.core.services import diagnosis_quick_input
from apps.core.services.diagnosis_quick_input import QuickRunError, normalize_input

RID = "12345678-1234-5678-1234-567812345678"


class QuickInputTests(unittest.TestCase):
    def test_saved_run_accepts_one_id_and_rejects_configuration_overrides(self):
        body = {"request_id": RID, "trace_ids": ["b", "a"], "diagnoser_uuid": "saved-1"}
        value = diagnosis_quick_input.normalize_run_input(body, "agent")
        self.assertEqual(
            value, {**body, "trace_ids": ["a", "b"], "agent_uuid": "agent"}
        )
        for patch in (
            {"spec": {}},
            {"model_connection_uuid": "model"},
            {"before_task_uuid": "old"},
            {"diagnoser_uuids": ["saved-1"]},
            {"diagnoser_uuid": ""},
            {"trace_ids": ["a"] * 11},
        ):
            with self.subTest(patch=patch), self.assertRaises(QuickRunError):
                diagnosis_quick_input.normalize_run_input({**body, **patch}, "agent")

    def test_retest_only_needs_anchor_and_selection(self):
        value = normalize_input(
            {"request_id": RID, "trace_ids": ["b", "a"], "before_task_uuid": "t-old"},
            "agent",
        )
        self.assertEqual(value["trace_ids"], ["a", "b"])
        self.assertNotIn("scene_key", value)

    def test_first_run_normalizes_sets_not_duplicates(self):
        value = normalize_input(
            {
                "request_id": RID,
                "trace_ids": ["b", "a"],
                "builtin_keys": ["tool_success"],
            },
            "agent",
        )
        self.assertEqual(value["scene_key"], "quick_runtime")
        for patch in (
            {"trace_ids": ["a", "a"]},
            {"spec": {}},
            {"trace_ids": []},
            {"trace_ids": ["a"] * 11},
        ):
            with self.assertRaises(QuickRunError):
                normalize_input(
                    {k: v for k, v in {**value, **patch}.items() if k != "agent_uuid"},
                    "agent",
                )

    def test_before_rejects_current_defaults_and_owner_override(self):
        for patch in (
            {"scene_key": "quick_runtime"},
            {"builtin_keys": []},
            {"model_connection_uuid": "m"},
            {"owner_id": "u"},
        ):
            with self.assertRaises(QuickRunError):
                normalize_input(
                    {
                        "request_id": RID,
                        "trace_ids": ["a"],
                        "before_task_uuid": "t",
                        **patch,
                    },
                    "agent",
                )

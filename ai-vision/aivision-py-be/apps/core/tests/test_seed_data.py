from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import AgentTypeConfig


class SeedDataTests(TestCase):
    def test_seed_only_backfills_retired_types_that_already_exist(self):
        AgentTypeConfig.objects.filter(
            agent_name__in=["CODEFUSE", "HOMICLAW"]
        ).delete()

        call_command("seed_data", stdout=StringIO())

        self.assertFalse(
            AgentTypeConfig.objects.filter(
                agent_name__in=["CODEFUSE", "HOMICLAW"]
            ).exists()
        )

        legacy = AgentTypeConfig.objects.create(
            agent_name="HOMICLAW",
            agent_product="HomiClaw",
            display_name="Team Agent",
            icon="/custom.svg",
        )

        call_command("seed_data", stdout=StringIO())

        legacy.refresh_from_db()
        self.assertEqual(legacy.runtime_type, "openclaw")
        self.assertEqual(legacy.display_name, "Team Agent")
        self.assertEqual(legacy.icon, "/custom.svg")
        self.assertFalse(AgentTypeConfig.objects.filter(agent_name="CODEFUSE").exists())

    def test_legacy_claude_code_seed_uses_a_public_label_and_keeps_custom_labels(self):
        AgentTypeConfig.objects.filter(agent_name="ANT_CLAUDE_CODE").delete()

        call_command("seed_data", stdout=StringIO())

        agent_type = AgentTypeConfig.objects.get(agent_name="ANT_CLAUDE_CODE")
        self.assertEqual(agent_type.agent_product, "Ant Claude Code")
        self.assertEqual(agent_type.display_name, "Claude Code")

        agent_type.display_name = "Team Claude Code"
        agent_type.save(update_fields=["display_name"])
        call_command("seed_data", stdout=StringIO())

        agent_type.refresh_from_db()
        self.assertEqual(agent_type.display_name, "Team Claude Code")

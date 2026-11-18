"""Startup must preserve operator-owned cache settings across redeploys."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.core.models import PlatformConfig


class PlatformConfigUpgradeTests(TestCase):
    def test_default_startup_preserves_existing_operator_settings(self):
        original = {"cache_ttl": 997, "cache_version": 12, "custom": {"enabled": False}}
        row = PlatformConfig.objects.create(
            platform_code="system", platform_name="System", config=original
        )
        for _ in range(2):
            call_command("ensure_platform_config", stdout=StringIO())
            row.refresh_from_db()
            self.assertEqual(row.config, original)

    def test_explicit_ttl_changes_only_requested_setting(self):
        row = PlatformConfig.objects.create(
            platform_code="system", platform_name="System",
            config={"cache_ttl": 997, "cache_version": 12, "custom": False},
        )
        call_command("ensure_platform_config", "--cache-ttl", "600", stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual(row.config, {"cache_ttl": 600, "cache_version": 12, "custom": False})

    def test_new_and_partial_config_receive_bootstrap_defaults(self):
        call_command("ensure_platform_config", stdout=StringIO())
        row = PlatformConfig.objects.get(platform_code="system")
        self.assertEqual(row.config, {"cache_ttl": 3600, "cache_version": 0})
        row.config = {"custom": "keep"}
        row.save()
        call_command("ensure_platform_config", stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual(row.config, {"custom": "keep", "cache_ttl": 3600, "cache_version": 0})

from unittest.mock import patch

import requests
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.core.services.credential_provider import LangfuseCredentialProvider
from apps.core.views.frontend import FrontendIndexView


class UpstreamErrorRedactionTests(SimpleTestCase):
    @override_settings(FRONTEND_CDN_BASE="https://assets.example.test")
    @patch("apps.core.views.frontend.requests.get")
    def test_cdn_transport_error_exposes_only_public_message(self, get):
        get.side_effect = requests.ConnectionError("CDN client diagnostic detail")
        with self.assertLogs("apps.core.views.frontend", "ERROR") as logs:
            response = FrontendIndexView.as_view()(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 502)
        self.assertContains(response, "CDN unreachable", status_code=502)
        self.assertNotContains(response, "CDN client diagnostic detail", status_code=502)
        self.assertNotIn("CDN client diagnostic detail", " ".join(logs.output))

    @patch("apps.core.services.credential_provider.http_requests.delete")
    def test_failed_key_removal_does_not_log_identifier(self, delete):
        delete.side_effect = requests.Timeout()
        provider = LangfuseCredentialProvider("https://provider.example.test", "demo", "fixture")
        with self.assertLogs("apps.core.services.credential_provider", "WARNING") as logs:
            self.assertFalse(provider.delete_api_key("fixture-key-identifier"))
        self.assertNotIn("fixture-key-identifier", " ".join(logs.output))

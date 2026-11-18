"""HTTPS origin handling when ingress terminates TLS ahead of the web proxy."""

import os
import runpy
from unittest.mock import patch

from django.conf import settings
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.test import RequestFactory, SimpleTestCase, override_settings

from aivision_backend import settings as project_settings


@override_settings(ALLOWED_HOSTS=["vision.example"], CSRF_TRUSTED_ORIGINS=[])
class ProxyHeaderTests(SimpleTestCase):
    def proxy_setting(self, trust):
        # Load the actual deployment setting, including its default, instead of
        # hard-coding the setting tuple in each test.
        with patch.dict(os.environ):
            if trust is None:
                os.environ.pop("AIVISION_TRUST_PROXY_HEADERS", None)
            else:
                os.environ["AIVISION_TRUST_PROXY_HEADERS"] = trust
            return runpy.run_path(project_settings.__file__)["SECURE_PROXY_SSL_HEADER"]

    def request(self, origin, forwarded_proto=None):
        headers = {"HTTP_HOST": "vision.example:8443", "HTTP_ORIGIN": origin}
        if forwarded_proto is not None:
            headers["HTTP_X_FORWARDED_PROTO"] = forwarded_proto
        request = RequestFactory().post("/api/v1/auth/login", **headers)
        request.META["HTTP_X_CSRFTOKEN"] = get_token(request)
        request.COOKIES[settings.CSRF_COOKIE_NAME] = request.META["CSRF_COOKIE"]
        return request

    def check_csrf(self, request):
        view = lambda request: HttpResponse("ok")
        return CsrfViewMiddleware(view).process_view(request, view, (), {})

    def test_direct_http_ignores_forged_https_header_by_default(self):
        proxy_setting = self.proxy_setting(None)
        self.assertIsNone(proxy_setting)
        with override_settings(SECURE_PROXY_SSL_HEADER=proxy_setting):
            request = self.request("http://vision.example:8443", "https")
            self.assertFalse(request.is_secure())
            self.assertEqual(
                request.build_absolute_uri("/api/"), "http://vision.example:8443/api/"
            )
            self.assertIsNone(self.check_csrf(request))

    def test_trusted_https_preserves_urls_and_accepts_same_origin_csrf(self):
        with override_settings(SECURE_PROXY_SSL_HEADER=self.proxy_setting("true")):
            request = self.request("https://vision.example:8443", "https")
            self.assertTrue(request.is_secure())
            self.assertEqual(
                request.build_absolute_uri("/api/"), "https://vision.example:8443/api/"
            )
            self.assertIsNone(self.check_csrf(request))

    def test_trusted_proxy_still_rejects_cross_origin_csrf(self):
        with override_settings(SECURE_PROXY_SSL_HEADER=self.proxy_setting("true")):
            request = self.request("https://untrusted.example", "https")
            self.assertEqual(self.check_csrf(request).status_code, 403)

    def test_trusted_proxy_keeps_http_without_https_header(self):
        with override_settings(SECURE_PROXY_SSL_HEADER=self.proxy_setting("true")):
            for forwarded_proto in (None, "http"):
                with self.subTest(forwarded_proto=forwarded_proto):
                    request = self.request("http://vision.example:8443", forwarded_proto)
                    self.assertFalse(request.is_secure())
                    self.assertIsNone(self.check_csrf(request))

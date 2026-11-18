from django.http import HttpResponse
from django.test import TestCase, override_settings

from apps.core.auth_cookies import delete_auth_cookie, set_auth_cookie
from apps.core.views import (
    ObservationLinkDetailView,
    ShareDataView,
    ShareObservationView,
    TraceLinkDetailView,
    TraceLinkObservationDetailView,
)


class PublicSignedLinkAuthenticationTests(TestCase):
    def test_signed_link_views_disable_session_authentication(self):
        self.assertEqual(TraceLinkDetailView.authentication_classes, [])
        self.assertEqual(TraceLinkObservationDetailView.authentication_classes, [])
        self.assertEqual(ObservationLinkDetailView.authentication_classes, [])
        self.assertEqual(ShareDataView.authentication_classes, [])
        self.assertEqual(ShareObservationView.authentication_classes, [])


class AuthCookieAttributeTests(TestCase):
    @override_settings(AUTH_COOKIE_SECURE=True)
    def test_auth_cookie_attributes_use_local_session_policy(self):
        response = HttpResponse()
        set_auth_cookie(response, "aivision_access", "access", httponly=True)

        cookie = response.cookies["aivision_access"]
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertIs(cookie["secure"], True)
        self.assertEqual(cookie["path"], "/")
        self.assertIs(cookie["httponly"], True)

    @override_settings(AUTH_COOKIE_SECURE=True)
    def test_auth_cookie_delete_matches_local_session_policy(self):
        response = HttpResponse()
        delete_auth_cookie(response, "aivision_access")

        cookie = response.cookies["aivision_access"]
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/")

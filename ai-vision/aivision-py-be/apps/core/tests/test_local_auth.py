from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import AuthSession, Space, SpaceMember, User


@override_settings(
    LOCAL_ACCESS_TTL_SECONDS=900,
    LOCAL_REFRESH_TTL_SECONDS=1209600,
)
class LocalAuthAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient(enforce_csrf_checks=True)

    def csrf(self):
        response = self.client.get("/api/v1/auth/csrf")
        self.assertEqual(response.status_code, 200)
        return self.client.cookies["csrftoken"].value

    def register_account(self, *, name="Alice", email=" Alice@Example.COM ", **headers):
        response = self.client.post(
            "/api/v1/auth/register",
            {
                "name": name,
                "email": email,
                "password": "Correct-Horse-123!",
                "password_confirm": "Correct-Horse-123!",
            },
            format="json",
            HTTP_X_CSRFTOKEN=self.csrf(),
            **headers,
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response

    def test_first_registration_creates_account_personal_space_and_session(self):
        response = self.register_account()
        user = User.objects.get()
        self.assertFalse(hasattr(user, "work_id"))
        self.assertEqual(user.normalized_email, "alice@example.com")
        self.assertTrue(user.check_password("Correct-Horse-123!"))
        space = Space.objects.get(type=1)
        self.assertEqual(space.creator, user)
        self.assertEqual(space.name, "Alice Personal Space")
        self.assertEqual(space.scope, 1)
        self.assertTrue(
            SpaceMember.objects.filter(space=space, user=user, role=1).exists()
        )
        self.assertIn("aivision_access", response.cookies)
        self.assertIn("aivision_refresh", response.cookies)
        self.assertNotIn("access", response.data["data"])
        self.assertNotIn("refresh", response.data["data"])
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 200)

    def test_registration_localizes_only_the_generated_space_name(self):
        for i, (language, expected) in enumerate(
            (
                ("zh-CN", "Alice 的个人空间"),
                ("ja-JP", "Aliceの個人スペース"),
                ("en-US", "Alice Personal Space"),
                ("fr-FR", "Alice Personal Space"),
            )
        ):
            with self.subTest(language=language):
                result = self.register_account(
                    email=f"alice-{i}@example.com", HTTP_ACCEPT_LANGUAGE=language
                )
                self.assertEqual(
                    result.data["data"]["personal_space"]["name"], expected
                )

    def test_generated_space_name_fits_when_account_name_has_maximum_length(self):
        name = "空" * 128
        result = self.register_account(name=name, HTTP_ACCEPT_LANGUAGE="zh-CN")
        data = result.data["data"]
        self.assertEqual(data["user"]["name"], name)
        space_name = data["personal_space"]["name"]
        self.assertTrue(space_name.endswith("的个人空间"))
        self.assertEqual(len(space_name), 128)

    def test_registration_requires_csrf(self):
        no_csrf = self.client.post(
            "/api/v1/auth/register",
            {
                "name": "Alice",
                "email": "alice@example.com",
                "password": "Correct-Horse-123!",
                "password_confirm": "Correct-Horse-123!",
            },
            format="json",
        )
        self.assertEqual(no_csrf.status_code, 403)

    def test_product_guide_skip_persists_after_login(self):
        self.register_account()
        url = "/api/v1/user/me"
        self.assertIs(self.client.get(url).data["data"]["product_guide_pending"], True)

        for _ in range(2):
            cleared = self.client.patch(
                url,
                {"product_guide_pending": False},
                format="json",
                HTTP_X_CSRFTOKEN=self.csrf(),
            )
            self.assertEqual(cleared.status_code, 200, cleared.data)
            self.assertIs(cleared.data["data"]["product_guide_pending"], False)

        self.client = APIClient(enforce_csrf_checks=True)
        login = self.client.post(
            "/api/v1/auth/login",
            {"email": "alice@example.com", "password": "Correct-Horse-123!"},
            format="json",
            HTTP_X_CSRFTOKEN=self.csrf(),
        )
        self.assertEqual(login.status_code, 200, login.data)
        self.assertIs(self.client.get(url).data["data"]["product_guide_pending"], False)

    def test_clearing_product_guide_requires_session_csrf_and_only_accepts_false(self):
        url = "/api/v1/user/me"
        payload = {"product_guide_pending": False}
        csrf = self.csrf()
        anonymous = self.client.patch(
            url, payload, format="json", HTTP_X_CSRFTOKEN=csrf
        )
        self.assertEqual(anonymous.status_code, 401)

        self.register_account()
        no_csrf = self.client.patch(url, payload, format="json")
        self.assertEqual(no_csrf.status_code, 403)

        csrf = self.csrf()
        for invalid in (
            {},
            {"product_guide_pending": True},
            {"product_guide_pending": "false"},
            {"product_guide_pending": 0},
            {"product_guide_pending": None},
            {"product_guide_pending": False, "user_uuid": "another-user"},
            [payload],
        ):
            with self.subTest(invalid=invalid):
                response = self.client.patch(
                    url, invalid, format="json", HTTP_X_CSRFTOKEN=csrf
                )
                self.assertEqual(response.status_code, 400, response.data)
        self.assertIs(self.client.get(url).data["data"]["product_guide_pending"], True)

    def test_setup_api_is_removed(self):
        self.assertEqual(self.client.get("/api/v1/setup").status_code, 404)

    def test_login_refresh_and_logout_use_revocable_server_session(self):
        self.register_account()
        old_refresh = self.client.cookies["aivision_refresh"].value
        refreshed = self.client.post(
            "/api/v1/auth/refresh",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.assertNotEqual(old_refresh, self.client.cookies["aivision_refresh"].value)
        logout = self.client.post(
            "/api/v1/auth/logout",
            {},
            format="json",
            HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value,
        )
        self.assertEqual(logout.status_code, 200, logout.data)
        self.assertEqual(AuthSession.objects.get().status, AuthSession.STATUS_REVOKED)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, 401)

    @override_settings(PUBLIC_REGISTRATION_ENABLED=True)
    def test_public_registration_creates_isolated_personal_account(self):
        self.register_account()
        registration_client = APIClient(enforce_csrf_checks=True)
        csrf_response = registration_client.get("/api/v1/auth/csrf")
        response = registration_client.post(
            "/api/v1/auth/register",
            {
                "name": "Bob",
                "email": "bob@example.com",
                "password": "Correct-Horse-456!",
                "password_confirm": "Correct-Horse-456!",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_response.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 201, response.data)
        bob = User.objects.get(normalized_email="bob@example.com")
        personal_space = Space.objects.get(creator=bob, type=1)
        self.assertTrue(
            SpaceMember.objects.filter(
                space=personal_space, user=bob, role=1, gmt_deleted__isnull=True
            ).exists()
        )
        self.assertEqual(
            SpaceMember.objects.filter(user=bob, gmt_deleted__isnull=True).count(), 1
        )
        self.assertIn("aivision_access", response.cookies)
        self.assertIn("aivision_refresh", response.cookies)

    @override_settings(PUBLIC_REGISTRATION_ENABLED=False)
    def test_public_registration_can_be_disabled_by_deployment(self):
        response = self.client.post(
            "/api/v1/auth/register",
            {
                "name": "Bob",
                "email": "bob@example.com",
                "password": "Correct-Horse-456!",
                "password_confirm": "Correct-Horse-456!",
            },
            format="json",
            HTTP_X_CSRFTOKEN=self.csrf(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.data["data"]["error_key"], "PUBLIC_REGISTRATION_DISABLED"
        )

    def test_login_failure_does_not_reveal_whether_email_exists(self):
        self.register_account()
        csrf = self.client.cookies["csrftoken"].value
        existing = self.client.post(
            "/api/v1/auth/login",
            {"email": "alice@example.com", "password": "wrong"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        missing = self.client.post(
            "/api/v1/auth/login",
            {"email": "missing@example.com", "password": "wrong"},
            format="json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(existing.status_code, missing.status_code)
        self.assertEqual(existing.data, missing.data)

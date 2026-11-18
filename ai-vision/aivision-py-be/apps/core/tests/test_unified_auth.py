"""UnifiedExternalAuthentication 单元测试。"""

import base64

from django.test import RequestFactory
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from apps.core.authentication import UnifiedExternalAuthentication
from apps.core.models import (
    ObservationEndpointConfig,
    Space,
    SpaceKey,
    SpaceObservationCredential,
    SpaceObservationProject,
    User,
)
from apps.core.services.credential_service import hash_secret
from apps.core.tests.helpers import create_local_user, SpaceKeyTestCase


class UnifiedExternalAuthenticationTest(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.auth = UnifiedExternalAuthentication()
        self.user = create_local_user("100001", name="test")
        self.space = Space.objects.create(
            name="test-space", type=2, scope=1, creator=self.user, status=1
        )

        self.raw_key, self.key_prefix, self.key_hash = SpaceKey.generate_key()
        self.space_key = SpaceKey.objects.create(
            space=self.space,
            key_prefix=self.key_prefix,
            key_hash=self.key_hash,
            name="test-key",
            creator=self.user,
            enabled=True,
        )

    # --- Bearer SpaceKey ---

    def test_bearer_space_key_success(self):
        request = self.factory.get(
            "/openapi/v1/test",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",
        )
        result = self.auth.authenticate(request)
        self.assertIsNotNone(result)
        user, auth = result
        self.assertIsNone(user)
        self.assertEqual(auth.id, self.space_key.id)
        self.assertEqual(request.auth_type, "space_key")

    def test_bearer_space_key_invalid(self):
        request = self.factory.get(
            "/openapi/v1/test",
            HTTP_AUTHORIZATION="Bearer sk-aiv-00000000000000000000000000000000",
        )
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    def test_bearer_space_key_disabled(self):
        self.space_key.enabled = False
        self.space_key.save()
        request = self.factory.get(
            "/openapi/v1/test",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",
        )
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    # --- Bearer non-SpaceKey is rejected ---

    def test_bearer_non_space_key_is_rejected(self):
        """The external boundary accepts only its declared credentials."""
        from rest_framework.exceptions import AuthenticationFailed

        request = self.factory.get(
            "/openapi/v1/test",
            HTTP_AUTHORIZATION="Bearer some-jwt-token",
        )
        with self.assertRaises((AuthenticationFailed, Exception)):
            self.auth.authenticate(request)

    # --- Unsupported SpaceKey headers ---

    def test_x_aivision_key_header_is_ignored(self):
        request = self.factory.get(
            "/openapi/v1/test",
            HTTP_X_AIVISION_KEY=self.raw_key,
        )
        result = self.auth.authenticate(request)
        self.assertIsNone(result)
        self.assertFalse(hasattr(request, "auth_type"))

    def test_x_space_key_header_is_ignored(self):
        request = self.factory.get(
            "/openapi/v1/test",
            HTTP_X_SPACE_KEY=self.raw_key,
        )
        result = self.auth.authenticate(request)
        self.assertIsNone(result)
        self.assertFalse(hasattr(request, "auth_type"))

    # --- No header → None ---

    def test_no_header_returns_none(self):
        request = self.factory.get("/openapi/v1/test")
        result = self.auth.authenticate(request)
        self.assertIsNone(result)

    # --- Basic Auth (Credential) ---

    def test_basic_auth_credential_success(self):
        secret = "sk-read-test"
        endpoint = ObservationEndpointConfig.objects.create(
            name="local",
            base_url="https://langfuse.example.com",
            tenant_name="local",
            status=1,
        )
        project = SpaceObservationProject.objects.create(
            space=self.space,
            endpoint_config=endpoint,
            tenant_name="local",
            status=1,
            provision_status=1,
        )
        credential = SpaceObservationCredential.objects.create(
            space_observation_project=project,
            scope="READ",
            public_key="pk-read-test",
            secret_hash=hash_secret(secret),
            secret_ciphertext="unused-in-auth",
            status=1,
        )
        encoded = base64.b64encode(
            f"{credential.public_key}:{secret}".encode()
        ).decode()
        request = self.factory.get(
            "/openapi/v1/test",
            HTTP_AUTHORIZATION=f"Basic {encoded}",
        )

        result = self.auth.authenticate(request)

        self.assertIsNotNone(result)
        user, auth = result
        self.assertIsNone(user)
        self.assertEqual(auth.id, credential.id)
        self.assertEqual(request.auth_type, "credential")

    def test_basic_auth_write_credential_denied(self):
        secret = "sk-write-test"
        endpoint = ObservationEndpointConfig.objects.create(
            name="local-write",
            base_url="https://langfuse.example.com",
            tenant_name="local",
            status=1,
        )
        project = SpaceObservationProject.objects.create(
            space=self.space,
            endpoint_config=endpoint,
            tenant_name="local",
            status=1,
            provision_status=1,
        )
        credential = SpaceObservationCredential.objects.create(
            space_observation_project=project,
            scope="WRITE",
            public_key="pk-write-test",
            secret_hash=hash_secret(secret),
            secret_ciphertext="unused-in-auth",
            status=1,
        )
        encoded = base64.b64encode(
            f"{credential.public_key}:{secret}".encode()
        ).decode()
        request = self.factory.get(
            "/openapi/v1/test",
            HTTP_AUTHORIZATION=f"Basic {encoded}",
        )

        with self.assertRaises(PermissionDenied):
            self.auth.authenticate(request)

    def test_basic_auth_invalid_format(self):
        request = self.factory.get(
            "/api/public/traces",
            HTTP_AUTHORIZATION="Basic not-valid-base64!!!",
        )
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    def test_basic_auth_invalid_credential(self):
        encoded = base64.b64encode(b"pk_invalid:sk_invalid").decode()
        request = self.factory.get(
            "/api/public/traces",
            HTTP_AUTHORIZATION=f"Basic {encoded}",
        )
        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(request)

    # --- authenticate_header ---

    def test_authenticate_header_returns_bearer(self):
        request = self.factory.get("/test")
        self.assertEqual(self.auth.authenticate_header(request), "Bearer")

    def test_bearer_space_key_ignores_unsupported_extra_header(self):
        request = self.factory.get(
            "/openapi/v1/test",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",
            HTTP_X_AIVISION_KEY="sk-aiv-ignored",
        )
        result = self.auth.authenticate(request)
        self.assertIsNotNone(result)
        self.assertEqual(request.auth_type, "space_key")

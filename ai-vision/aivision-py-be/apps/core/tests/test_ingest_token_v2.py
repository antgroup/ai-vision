from datetime import datetime, timezone
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.services.ingest_token import build_ingest_token, verify_ingest_token


VECTOR_SECRET = b"vector-secret"
VECTOR_TOKEN = (
    "eyJ2IjoyLCJzcCI6InNwYWNlLXZlY3RvciIsInUiOiJzdWJqZWN0LXZlY3RvciIs"
    "ImtpZCI6ImtleS12ZWN0b3IiLCJhaWQiOiJhZ2VudC12ZWN0b3IiLCJhcCI6IkNv"
    "ZGV4IiwiaWF0IjoxNzAwMDAwMDAwLCJleHAiOjQxMDI0NDQ4MDAsIm9yZyI6Im9y"
    "Zy12ZWN0b3IifQ.Y_Yuv6N03Oc1x5kqgsP5d1KX4xFyoi38VsnWbsrvkU4"
)


class IngestTokenV2VectorTests(SimpleTestCase):
    def test_python_signer_matches_shared_fixed_vector(self):
        now = datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
        with patch("apps.core.services.ingest_token.timezone.now", return_value=now):
            token, expires_at = build_ingest_token(
                space_uuid="space-vector",
                org_id="org-vector",
                subject_id="subject-vector",
                key_id="key-vector",
                agent_uuid="agent-vector",
                agent_product="Codex",
                ttl_seconds=2_402_444_800,
                secret=VECTOR_SECRET,
            )
        self.assertEqual(expires_at, 4_102_444_800)
        self.assertEqual(token, VECTOR_TOKEN)

    def test_python_verifier_accepts_shared_fixed_vector(self):
        claims = verify_ingest_token(VECTOR_TOKEN, secret=VECTOR_SECRET)
        self.assertEqual(claims["v"], 2)
        self.assertEqual(claims["sp"], "space-vector")
        self.assertEqual(claims["u"], "subject-vector")
        self.assertEqual(claims["aid"], "agent-vector")
        self.assertEqual(claims["ap"], "Codex")

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from observ_hermes.privacy import MAX_STRING_LENGTH, REDACTED, hide_content, sanitize


class PrivacyTests(unittest.TestCase):
    def test_recursive_secret_redaction(self) -> None:
        secret = "super-secret-value"
        value = {
            "nested": {
                "Authorization": f"Bearer {secret}",
                "items": [{"password": secret}, {"message": f"token={secret}"}],
            },
            "safe": "kept",
        }
        projected = sanitize(value)
        serialized = json.dumps(projected.value)
        self.assertTrue(projected.redacted)
        self.assertNotIn(secret, serialized)
        self.assertEqual(projected.value["nested"]["Authorization"], REDACTED)
        self.assertEqual(projected.value["safe"], "kept")

    def test_numeric_usage_tokens_are_preserved_but_credentials_named_token_are_not(
        self,
    ) -> None:
        secret = "credential-value"
        projected = sanitize(
            {
                "usage": {
                    "input_tokens": 123,
                    "output_tokens": 45,
                    "reasoning_tokens": 6,
                },
                "token": secret,
                "access_token": secret,
            }
        )
        self.assertEqual(
            projected.value["usage"],
            {
                "input_tokens": 123,
                "output_tokens": 45,
                "reasoning_tokens": 6,
            },
        )
        self.assertEqual(projected.value["token"], REDACTED)
        self.assertEqual(projected.value["access_token"], REDACTED)
        self.assertNotIn(secret, json.dumps(projected.value))

    def test_generic_message_fields_redact_private_keys_cookies_and_prefixed_tokens(
        self,
    ) -> None:
        credentials = [
            "-----BEGIN PRIVATE KEY-----\n"
            "private-key-material\n"
            "-----END PRIVATE KEY-----",
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "openssh-key-material\n"
            "-----END OPENSSH PRIVATE KEY-----",
            "Cookie: session=very-secret; theme=dark",
            "Set-Cookie: refresh=another-secret; HttpOnly; Secure",
            "ghp_abcdefghijklmnopqrstuvwxyz123456",
            "github_pat_11_AAabcdefghijklmnopqrstuvwxyz123456",
            "xoxb-ci-fixture",
            "AKIAABCDEFGHIJKLMNOP",
            "AIzaabcdefghijklmnopqrstuvwxyz123456789",
            "sk_test_fixtureonly00000",
            "hf_abcdefghijklmnopqrstuvwxyz123456",
        ]
        projected = sanitize({"message": "\n".join(credentials), "error": credentials})
        rendered = json.dumps(projected.value)
        self.assertTrue(projected.redacted)
        for credential in credentials:
            self.assertNotIn(credential, rendered)
        for fragment in (
            "private-key-material",
            "openssh-key-material",
            "very-secret",
            "another-secret",
            "ghp_",
            "github_pat_",
            "xoxb-",
            "AKIA",
            "AIza",
            "sk_test_",
            "hf_",
        ):
            self.assertNotIn(fragment, rendered)

    def test_oversized_credentials_are_fully_redacted_before_truncation(self) -> None:
        credentials = {
            "pem": (
                "-----BEGIN PRIVATE KEY-----\n"
                + "P" * 70_000
                + "\n-----END PRIVATE KEY-----"
            ),
            "cookie": "Cookie: session=" + "C" * 40_000 + "; Secure",
            "set_cookie": "Set-Cookie: refresh=" + "S" * 40_000 + "; HttpOnly",
            "bearer": "Bearer " + "B" * 40_000,
            "basic": "Basic " + "Q" * 40_000,
            "prefixed": "ghp_" + "G" * 40_000,
        }
        projected = sanitize({"message": "\n".join(credentials.values())})
        rendered = projected.value["message"]
        self.assertTrue(projected.redacted)
        self.assertFalse(projected.truncated)
        self.assertEqual(rendered.count(REDACTED), len(credentials))
        for marker in ("P" * 64, "C" * 64, "S" * 64, "B" * 64, "Q" * 64, "G" * 64):
            self.assertNotIn(marker, rendered)

    def test_ordinary_prose_that_mentions_tokens_and_cookies_is_not_over_redacted(
        self,
    ) -> None:
        prose = (
            "The tokenizer counts input tokens. Cookies are small browser records. "
            "Rotate API keys regularly, and never paste a private key into chat."
        )
        projected = sanitize({"message": prose})
        self.assertEqual(projected.value["message"], prose)
        self.assertFalse(projected.redacted)

    def test_structured_truncation_remains_valid_json(self) -> None:
        projected = sanitize(
            {"content": "x" * (MAX_STRING_LENGTH + 10), "items": list(range(120))}
        )
        encoded = json.dumps(projected.value)
        self.assertEqual(json.loads(encoded), projected.value)
        self.assertTrue(projected.truncated)
        self.assertLess(len(projected.value["content"]), MAX_STRING_LENGTH + 10)

    def test_privacy_marker_is_stable_and_non_reversible(self) -> None:
        first = hide_content({"prompt": "private"})
        second = hide_content({"prompt": "private"})
        self.assertEqual(first.value, second.value)
        self.assertNotIn("private", first.value)
        self.assertTrue(first.redacted)


if __name__ == "__main__":
    unittest.main()

class MediaPrivacyTests(unittest.TestCase):
    def test_json_schema_union_types_remain_ordinary_structured_content(self):
        schema = {"type": ["integer", "null"], "title": "Nullable tool output"}
        projected = sanitize({"result": schema})
        self.assertEqual(projected.value["result"], schema)

    def test_malformed_audio_format_is_an_invalid_media_descriptor(self):
        from aivision_hermes_core.media import media_block
        for audio_format in ({}, [], None):
            with self.subTest(audio_format=audio_format):
                self.assertEqual(
                    media_block({"type": "input_audio", "input_audio": {"format": audio_format, "data": "YWJj"}}),
                    {"type": "aivision_media_error", "reason": "invalid"},
                )

    def test_explicit_media_preserves_bytes_but_plain_strings_remain_bounded(self):
        import base64
        from aivision_hermes_core.media import media_block, MAX_MEDIA_BYTES
        data = base64.b64encode(b'x' * 24000).decode()
        for mime in ('image/png', 'audio/ogg', 'video/mp4', 'application/pdf', 'text/markdown'):
            projected = sanitize({'content': [{'type': 'media', 'source': {'type': 'base64', 'media_type': mime, 'data': data}}], 'password': 'private-secret'})
            self.assertEqual(projected.value['content'][0]['source']['data'], data)
            self.assertNotIn('private-secret', json.dumps(projected.value))
            self.assertNotIn(data, hide_content(projected.value).value)
        self.assertTrue(sanitize(data).truncated)
        self.assertIsNone(media_block({'data': data}))
        self.assertEqual(media_block({'type': 'audio', 'mime_type': 'audio/wav', 'data': 'invalid'})['reason'], 'invalid')
        exact = base64.b64encode(b'x' * MAX_MEDIA_BYTES).decode()
        self.assertEqual(media_block({'type':'audio','mime_type':'audio/wav','data':exact})['source']['data'], exact)
        self.assertEqual(media_block({'type':'audio','mime_type':'audio/wav','data':base64.b64encode(b'x' * (MAX_MEDIA_BYTES+1)).decode()})['reason'], 'budget')

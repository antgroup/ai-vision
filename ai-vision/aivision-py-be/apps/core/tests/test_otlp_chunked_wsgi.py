from io import BytesIO
import gzip

from django.test import SimpleTestCase, override_settings

from aivision_backend.otlp_wsgi import ChunkedOtlpBody
from apps.core.views.langfuse_ingest import normalize_langfuse_otlp_payload


class ChunkedOtlpBodyTests(SimpleTestCase):
    @override_settings(AIVISION_OTLP_MEDIA_REQUEST_MAX_BYTES=1024 * 1024 + 4)
    def test_chunked_body_is_bounded_and_made_available_to_django(self):
        def application(environ, start_response):
            start_response("200 OK", [])
            return [environ["wsgi.input"].read(int(environ["CONTENT_LENGTH"]))]

        def run(body):
            status = []
            environ = {
                "PATH_INFO": "/api/public/otel/v1/traces",
                "REQUEST_METHOD": "POST",
                "HTTP_TRANSFER_ENCODING": "chunked",
                "wsgi.input": BytesIO(body),
            }
            response = ChunkedOtlpBody(application)(
                environ, lambda code, headers: status.append(code)
            )
            return status[0], b"".join(response)

        limit = 4 + 1024 * 1024
        self.assertEqual(run(b"x" * limit), ("200 OK", b"x" * limit))
        self.assertEqual(run(b"x" * (limit + 1)), ("413 Payload Too Large", b""))

    def test_empty_protobuf_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_langfuse_otlp_payload(b"", None)

    @override_settings(AIVISION_OTLP_MEDIA_REQUEST_MAX_BYTES=1024 * 1024 + 4)
    def test_compressed_payload_cannot_bypass_ingest_budget(self):
        with self.assertRaisesRegex(ValueError, "decompressed OTLP payload exceeds limit"):
            normalize_langfuse_otlp_payload(gzip.compress(b"x" * (1024 * 1024 + 5)), "gzip")

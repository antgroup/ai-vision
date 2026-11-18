"""Accept bounded chunked OTLP requests before Django applies CONTENT_LENGTH."""

from io import BytesIO

from django.conf import settings


class ChunkedOtlpBody:
    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        if (
            environ.get("PATH_INFO") == "/api/public/otel/v1/traces"
            and environ.get("REQUEST_METHOD") == "POST"
            and not environ.get("CONTENT_LENGTH")
            and environ.get("HTTP_TRANSFER_ENCODING", "").lower() == "chunked"
        ):
            limit = settings.AIVISION_OTLP_MEDIA_REQUEST_MAX_BYTES
            data = environ["wsgi.input"].read(limit + 1)
            if len(data) > limit:
                start_response("413 Payload Too Large", [("Content-Length", "0")])
                return [b""]
            environ["wsgi.input"] = BytesIO(data)
            environ["CONTENT_LENGTH"] = str(len(data))
        return self.application(environ, start_response)

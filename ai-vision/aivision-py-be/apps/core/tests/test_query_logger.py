import logging
import os
import sys
import tempfile
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.test import SimpleTestCase

from aivision_backend.logging_context import (
    RequestContextFormatter,
    RequestLoggingContextMiddleware,
    get_error_log_handler,
    resolve_log_dir,
)
from apps.core.query_logger import _get_or_create_file_logger


class RequestContextFormatterTests(SimpleTestCase):
    def test_preserves_message_and_traceback_line_breaks(self):
        formatter = RequestContextFormatter(
            format="{levelname} {request_context}{message}",
            style="{",
        )

        try:
            raise ValueError("upstream\r\nfailed")
        except ValueError:
            record = logging.LogRecord(
                name="query_events",
                level=logging.ERROR,
                pathname=__file__,
                lineno=18,
                msg="query failed\nSQL: SELECT 1\r\nFROM dual",
                args=(),
                exc_info=sys.exc_info(),
            )

        formatted = formatter.format(record)

        self.assertIn("query failed\nSQL: SELECT 1\r\nFROM dual", formatted)
        self.assertIn("ValueError: upstream\r\nfailed", formatted)
        self.assertIn("Traceback (most recent call last):\n", formatted)

    def test_adds_authenticated_request_user_and_resets_after_response(self):
        formatter = RequestContextFormatter(
            format="{levelname} {request_context}{message}",
            style="{",
        )
        request = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True, uuid="100001"),
            resolver_match=SimpleNamespace(kwargs={"space_id": "space-001"}),
        )

        def get_response(_request):
            record = logging.LogRecord(
                name="apps.core.views.v2",
                level=logging.ERROR,
                pathname=__file__,
                lineno=45,
                msg="query failed",
                args=(),
                exc_info=None,
            )
            return formatter.format(record)

        middleware = RequestLoggingContextMiddleware(get_response)

        self.assertEqual(
            middleware(request),
            "ERROR [user:100001|space:space-001] query failed",
        )

        outside_record = logging.LogRecord(
            name="apps.core.views.v2",
            level=logging.ERROR,
            pathname=__file__,
            lineno=60,
            msg="outside request",
            args=(),
            exc_info=None,
        )
        self.assertEqual(formatter.format(outside_record), "ERROR outside request")

    def test_uses_explicit_business_log_context(self):
        formatter = RequestContextFormatter(
            format="{levelname} {request_context}{message}",
            style="{",
        )
        record = logging.LogRecord(
            name="query_traces",
            level=logging.ERROR,
            pathname=__file__,
            lineno=75,
            msg="Stargazer query FAILED",
            args=(),
            exc_info=None,
        )
        record.current_user = "100001"
        record.current_space = "space-001"

        self.assertEqual(
            formatter.format(record),
            "ERROR [user:100001|space:space-001] Stargazer query FAILED",
        )

    def test_escapes_request_context_format_delimiters(self):
        formatter = RequestContextFormatter(
            format="{levelname} {request_context}{message}",
            style="{",
        )
        record = logging.LogRecord(
            name="query_traces",
            level=logging.ERROR,
            pathname=__file__,
            lineno=90,
            msg="Stargazer query FAILED",
            args=(),
            exc_info=None,
        )
        record.current_user = r"user\name[100001]|admin"
        record.current_space = "space[001]\r\nprod|primary"

        self.assertEqual(
            formatter.format(record),
            (
                r"ERROR [user:user\\name\[100001\]\|admin|"
                r"space:space\[001\]\r\nprod\|primary] Stargazer query FAILED"
            ),
        )

    def test_uses_request_attached_to_django_log_record(self):
        formatter = RequestContextFormatter(
            format="{levelname} {request_context}{message}",
            style="{",
        )
        record = logging.LogRecord(
            name="django.request",
            level=logging.ERROR,
            pathname=__file__,
            lineno=95,
            msg="Internal Server Error: /api/v2/query",
            args=(),
            exc_info=None,
        )
        record.request = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True, uuid="100001"),
            resolver_match=SimpleNamespace(kwargs={"space_name": "team-space"}),
        )

        self.assertEqual(
            formatter.format(record),
            "ERROR [user:100001|space:team-space] Internal Server Error: /api/v2/query",
        )

    def test_async_request_context_is_available_until_response_finishes(self):
        formatter = RequestContextFormatter(
            format="{levelname} {request_context}{message}",
            style="{",
        )
        request = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True, uuid="100001"),
            resolver_match=SimpleNamespace(kwargs={"space_id": "space-001"}),
        )

        async def get_response(_request):
            record = logging.LogRecord(
                name="apps.core.views.v2",
                level=logging.ERROR,
                pathname=__file__,
                lineno=102,
                msg="async query failed",
                args=(),
                exc_info=None,
            )
            return formatter.format(record)

        middleware = RequestLoggingContextMiddleware(get_response)

        self.assertEqual(
            async_to_sync(middleware)(request),
            "ERROR [user:100001|space:space-001] async query failed",
        )

    def test_request_context_uses_placeholders_for_missing_values(self):
        formatter = RequestContextFormatter(
            format="{levelname} {request_context}{message}",
            style="{",
        )
        request = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=False),
            resolver_match=SimpleNamespace(kwargs={}),
            GET={},
        )

        def get_response(_request):
            record = logging.LogRecord(
                name="django.request",
                level=logging.INFO,
                pathname=__file__,
                lineno=170,
                msg="request started",
                args=(),
                exc_info=None,
            )
            return formatter.format(record)

        self.assertEqual(
            RequestLoggingContextMiddleware(get_response)(request),
            "INFO [user:-|space:-] request started",
        )


class BusinessFileLoggerTests(SimpleTestCase):
    def test_resolve_log_dir_uses_environment_override(self):
        with patch.dict(os.environ, {"AIVISION_LOG_DIR": "/tmp/aivision-test-logs"}):
            self.assertEqual(resolve_log_dir(), "/tmp/aivision-test-logs")

    def test_error_handler_configuration_is_owned_by_factory(self):
        self.assertEqual(
            settings.LOGGING["handlers"]["error_file"],
            {"()": "aivision_backend.logging_context.get_error_log_handler"},
        )

        handler = get_error_log_handler()

        self.assertEqual(handler.level, logging.ERROR)
        self.assertIsInstance(handler.formatter, RequestContextFormatter)

    def test_error_records_are_aggregated_without_hiding_original_log(self):
        logger_name = f"test_business_file_logger_{id(self)}"
        logger = logging.getLogger(logger_name)
        error_stream = StringIO()
        error_handler = logging.StreamHandler(error_stream)
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(
            RequestContextFormatter(
                format="{levelname} {request_context}{message}",
                style="{",
            )
        )

        with (
            tempfile.TemporaryDirectory() as log_dir,
            patch("apps.core.query_logger.resolve_log_dir", return_value=log_dir),
            patch(
                "apps.core.query_logger.get_error_log_handler",
                return_value=error_handler,
            ),
        ):
            try:
                configured_logger = _get_or_create_file_logger(
                    logger_name, "business.log"
                )
                self.assertIs(configured_logger, logger)
                log_context = {
                    "current_user": "100001",
                    "current_space": "space-001",
                }
                configured_logger.info("successful query", extra=log_context)
                configured_logger.error(
                    "failed query\nupstream detail",
                    extra=log_context,
                )
                for handler in configured_logger.handlers:
                    handler.flush()

                with open(
                    os.path.join(log_dir, "business.log"),
                    encoding="utf-8",
                ) as source_log:
                    source_contents = source_log.read()

                self.assertIn("successful query", source_contents)
                self.assertIn("failed query\nupstream detail", source_contents)
                self.assertIn(
                    "[user:100001|space:space-001] successful query",
                    source_contents,
                )
                self.assertNotIn("successful query", error_stream.getvalue())
                self.assertIn(
                    "[user:100001|space:space-001] failed query\nupstream detail",
                    error_stream.getvalue(),
                )
            finally:
                for handler in logger.handlers[:]:
                    logger.removeHandler(handler)
                    if handler is not error_handler:
                        handler.close()

    def test_business_logger_ignores_external_handlers_and_is_idempotent(self):
        logger_name = f"test_business_file_logger_external_{id(self)}"
        logger = logging.getLogger(logger_name)
        external_handler = logging.NullHandler()
        logger.addHandler(external_handler)
        error_handler = logging.NullHandler()

        with (
            tempfile.TemporaryDirectory() as log_dir,
            patch("apps.core.query_logger.resolve_log_dir", return_value=log_dir),
            patch(
                "apps.core.query_logger.get_error_log_handler",
                return_value=error_handler,
            ),
        ):
            try:
                first_logger = _get_or_create_file_logger(logger_name, "business.log")
                second_logger = _get_or_create_file_logger(logger_name, "business.log")

                self.assertIs(first_logger, logger)
                self.assertIs(second_logger, logger)
                self.assertIn(external_handler, logger.handlers)
                self.assertEqual(len(logger.handlers), 3)
                self.assertEqual(logger.handlers.count(error_handler), 1)

                first_logger.info("successful query")
                for handler in first_logger.handlers:
                    handler.flush()

                self.assertTrue(os.path.exists(os.path.join(log_dir, "business.log")))
            finally:
                for handler in logger.handlers[:]:
                    logger.removeHandler(handler)
                    if handler is not external_handler and handler is not error_handler:
                        handler.close()

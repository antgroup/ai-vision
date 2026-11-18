import socket
import threading
import time
from unittest.mock import patch

import mysql.connector
from django.db import OperationalError, connection
from django.db.utils import DatabaseErrorWrapper
from django.test import SimpleTestCase, TestCase


class ReadinessTests(TestCase):
    def test_ready_queries_database_without_authentication(self):
        # MySQL probes use a separate driver connection with bounded socket
        # timeouts, so Django's business-connection query log cannot see them.
        mysql = connection.vendor == "mysql"
        query_check = (
            patch.object(connection.Database, "connect", wraps=connection.Database.connect)
            if mysql else self.assertNumQueries(1)
        )
        with query_check as connect:
            response = self.client.get("/api/v1/ready")
        if mysql:
            connect.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class UnavailableReadinessTests(SimpleTestCase):
    @patch("aivision_backend.urls.connection")
    def test_unresponsive_mysql_handshake_is_bounded(self, connection):
        release = threading.Event()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(5)

            def stalled_server():
                with listener.accept()[0]:
                    release.wait(5)

            server = threading.Thread(target=stalled_server, daemon=True)
            server.start()
            connection.vendor = "mysql"
            connection.Database = mysql.connector
            connection.wrap_database_errors = DatabaseErrorWrapper(connection)
            connection.get_connection_params.return_value = {
                "host": "127.0.0.1", "port": listener.getsockname()[1], "user": "probe"
            }
            try:
                started = time.monotonic()
                response = self.client.get("/api/v1/ready")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"status": "unavailable"})
                self.assertLess(time.monotonic() - started, 4)
            finally:
                release.set()
                server.join(timeout=5)

    @patch("aivision_backend.urls.connection")
    def test_database_failure_is_unavailable_but_liveness_remains_ok(self, connection):
        connection.cursor.side_effect = OperationalError("secret-database-password")
        ready = self.client.get("/api/v1/ready")
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json(), {"status": "unavailable"})
        connection.cursor.assert_called_once_with()
        live = self.client.get("/api/v1/health")
        self.assertEqual(live.status_code, 200)
        connection.cursor.assert_called_once_with()

    @patch("aivision_backend.urls.connection")
    def test_unexpected_query_result_is_unavailable(self, connection):
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = None
        response = self.client.get("/api/v1/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})

    @patch("aivision_backend.urls.connection")
    def test_mysql_probe_has_separate_timeouts_and_closes_connection(self, connection):
        connection.vendor = "mysql"
        connection.get_connection_params.return_value = {"database": "aivision"}
        probe = connection.Database.connect.return_value
        probe.cursor.return_value.fetchone.return_value = (1,)
        response = self.client.get("/api/v1/ready")
        self.assertEqual(response.status_code, 200)
        connection.Database.connect.assert_called_once_with(
            database="aivision", connection_timeout=1, read_timeout=1, write_timeout=1
        )
        probe.cursor.return_value.execute.assert_called_once_with("SELECT 1")
        probe.cursor.return_value.close.assert_called_once_with()
        probe.close.assert_called_once_with()
        connection.cursor.assert_not_called()

"""Check shared SQL initialization deadlines and credential failure behavior."""
import importlib.util
import contextlib
import io
import os
from pathlib import Path
import types
import unittest
from unittest.mock import MagicMock, patch


class OperationalError(Exception):
    pass


class DatabaseInitializationTests(unittest.TestCase):
    def setUp(self):
        self.driver = types.SimpleNamespace(
            connect=MagicMock(),
            err=types.SimpleNamespace(OperationalError=OperationalError),
        )
        source = Path(__file__).resolve().parents[1] / "bootstrap/database-init.py"
        spec = importlib.util.spec_from_file_location("aivision_database_init", source)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"pymysql": self.driver}):
            spec.loader.exec_module(self.module)
        self.environment = patch.dict(os.environ, {
            "OB_HOST": "database.invalid", "OB_HOST_PORT": "2881",
            "OB_USER": "root", "OB_TENANT_PASSWORD": "private_fixture_password",
            "OB_ATTEMPTS": "2",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_success_uses_bounded_connection_and_query_io(self):
        connection = self.driver.connect.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [("OceanBase seekdb",), ("ob_compatibility_mode", "MYSQL")]
        self.module.main()
        options = self.driver.connect.call_args.kwargs
        self.assertEqual({key: options[key] for key in ("connect_timeout", "read_timeout", "write_timeout")},
                         {"connect_timeout": 30, "read_timeout": 30, "write_timeout": 30})
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(sum(statement.startswith("CREATE DATABASE IF NOT EXISTS") for statement in statements), 3)

    def test_wrong_credentials_fail_without_retry_or_secret_output(self):
        self.driver.connect.side_effect = OperationalError(1045, "private_fixture_password")
        with patch.object(self.module.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "configured credentials") as raised:
                self.module.main()
        self.assertNotIn("private_fixture_password", str(raised.exception))
        self.assertEqual(self.driver.connect.call_count, 1)
        sleep.assert_not_called()

    def test_imported_native_entrypoint_preserves_sanitized_failures(self):
        self.driver.connect.side_effect = ValueError("private_fixture_password")
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as raised:
            self.module.cli_main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("ValueError", output.getvalue())
        self.assertNotIn("private_fixture_password", output.getvalue())


if __name__ == "__main__":
    unittest.main()

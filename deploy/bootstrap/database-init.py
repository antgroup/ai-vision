#!/usr/bin/env python3
"""Verify the selected MySQL-mode datastore and idempotently create app databases."""
import os
import sys
import time

import pymysql


def main():
    host, port = os.environ["OB_HOST"], int(os.environ.get("OB_HOST_PORT", "2881"))
    user = os.environ.get("OB_USER") or "root@" + os.environ["OB_TENANT"]
    password = os.environ["OB_TENANT_PASSWORD"]
    last = None
    for attempt in range(int(os.environ.get("OB_ATTEMPTS", "90"))):
        try:
            connection = pymysql.connect(host=host, port=port, user=user, password=password,
                                         connect_timeout=30, read_timeout=30, write_timeout=30,
                                         charset="utf8mb4", autocommit=True)
            break
        except pymysql.err.OperationalError as error:
            last = error.args[0] if error.args else "unknown"
            if last in (1044, 1045):
                raise RuntimeError(f"database rejected configured credentials (SQL {last})") from None
            time.sleep(3)
    else:
        raise RuntimeError(f"database did not become ready (SQL {last})")
    with connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@version_comment")
            banner = str(cursor.fetchone()[0]).lower()
            if "oceanbase" not in banner and "seekdb" not in banner:
                raise RuntimeError("expected OceanBase CE or seekdb")
            cursor.execute("SHOW VARIABLES LIKE 'ob_compatibility_mode'")
            mode = cursor.fetchone()
            if mode and str(mode[1]).upper() != "MYSQL":
                raise RuntimeError("AI Vision requires a MySQL-mode tenant")
            for database in ("aivision", "signoz", "agentlens"):
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
    print("database authentication, compatibility and application databases verified")


def cli_main():
    try:
        main()
    except Exception as error:
        # Never echo a DSN, connection arguments or server response containing credentials.
        print(f"database initialization failed: {type(error).__name__}: "
              f"{error if isinstance(error, RuntimeError) else 'check database access and permissions'}",
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli_main()

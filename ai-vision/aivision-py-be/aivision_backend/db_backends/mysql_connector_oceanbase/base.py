"""Django 6 compatibility for MySQL Connector/Python 、seekdb and OceanBase CE."""

import re

from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.utils import NotSupportedError
from django.utils.functional import cached_property
from mysql.connector.django.base import (
    DatabaseWrapper as ConnectorDatabaseWrapper,
)
from mysql.connector.django.schema import (
    DatabaseSchemaEditor as ConnectorDatabaseSchemaEditor,
)

# Every OceanBase server shakes hands as MySQL 5.7, so Django's MySQL 8.0.11 floor
# rejects all of them; the product version is only readable from the version string.
# seekdb is a separate release line riding an older OceanBase kernel, so it carries
# its own pattern and its own floor instead of being squeezed into the CE one.
_OCEANBASE_CE_VERSION_RE = re.compile(
    r"\bOceanBase_CE(?:-v|\s+v?)(\d+)\.(\d+)\.(\d+)\.(\d+)\b",
    re.IGNORECASE,
)
_SEEKDB_VERSION_RE = re.compile(
    r"\bOceanBase[ _]seekdb(?:-v|\s+v?)(\d+)\.(\d+)\.(\d+)\.(\d+)\b",
    re.IGNORECASE,
)

_PRODUCT_FLOORS: tuple[tuple[str, re.Pattern[str], tuple[int, ...]], ...] = (
    ("OceanBase CE", _OCEANBASE_CE_VERSION_RE, (4, 3, 5, 5)),
    ("seekdb", _SEEKDB_VERSION_RE, (1, 3, 0, 0)),
)


def detect_product_version(
    version_text: str,
) -> tuple[str, tuple[int, ...], tuple[int, ...]] | None:
    """Return the advertised OceanBase product, its version and its floor."""
    for product, pattern, floor in _PRODUCT_FLOORS:
        match = pattern.search(version_text)
        if match:
            return product, tuple(int(part) for part in match.groups()), floor
    return None


class DatabaseSchemaEditor(ConnectorDatabaseSchemaEditor):
    """Restore proper string-literal quoting for Connector/Python.

    Connector/Python's ``quote_value`` returns the escaped value WITHOUT
    surrounding single quotes (its ``converter.escape`` only escapes, unlike
    mysqlclient/PyMySQL). Django's schema editor treats ``quote_value`` as a
    complete literal, so comments render as ``COMMENT 主键ID`` and every strict
    MySQL parser (OceanBase seekdb included) rejects them with 1064. Wrap the
    already-escaped value in single quotes so ``db_comment`` stays valid.
    """

    def quote_value(self, value):
        # super() escapes the value and doubles "%" so it survives the cursor's
        # %-interpolation, but leaves it unquoted.
        quoted = super().quote_value(value)
        if isinstance(value, str) and not (
            len(quoted) >= 2 and quoted[0] == "'" and quoted[-1] == "'"
        ):
            return f"'{quoted}'"
        return quoted


class DatabaseWrapper(ConnectorDatabaseWrapper):
    """Connector/Python backend with a narrow OceanBase compatibility gate."""

    SchemaEditorClass = DatabaseSchemaEditor

    @cached_property
    def display_name(self) -> str:
        return "MySQL"

    def get_database_version(self) -> tuple[int, ...]:
        return self.mysql_version

    def check_database_version_supported(self) -> None:
        version_text = str(self.mysql_server_data.get("version") or "")
        detected = detect_product_version(version_text)
        if detected is not None:
            product, version, floor = detected
            if version >= floor:
                return

            minimum = ".".join(map(str, floor))
            found = ".".join(map(str, version))
            raise NotSupportedError(
                f"{product} {minimum} or later is required (found {found})."
            )

        super().check_database_version_supported()

    def init_connection_state(self) -> None:
        BaseDatabaseWrapper.init_connection_state(self)
        super().init_connection_state()

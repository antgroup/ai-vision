"""Private, bounded production diagnostics for observ-hermes."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aivision_hermes_core.privacy import sanitize

from .config import ObservHermesConfig

LOG_FILE_NAME = "observ-hermes.log"
MAX_LOG_BYTES = 64 * 1024
BACKUP_COUNT = 2
_HANDLER_MARKER = "_observ_hermes_diagnostic_handler"
_PREVIOUS_LEVEL = "_observ_hermes_previous_level"
_PREVIOUS_PROPAGATE = "_observ_hermes_previous_propagate"


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """Rotating handler whose active and rotated files stay owner-only."""

    def _open(self):  # type: ignore[no-untyped-def]
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        descriptor = os.open(self.baseFilename, flags, 0o600)
        os.chmod(self.baseFilename, 0o600)
        return os.fdopen(descriptor, self.mode, encoding=self.encoding)

    def doRollover(self) -> None:
        super().doRollover()
        for index in range(1, self.backupCount + 1):
            backup = Path(f"{self.baseFilename}.{index}")
            if backup.exists():
                os.chmod(backup, 0o600)


class _SanitizingFilter(logging.Filter):
    """Redact credential-shaped values before formatting or persistence."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = str(sanitize(record.getMessage()).value)
        except Exception:
            record.msg = "[diagnostic formatting failed]"
        record.args = ()
        if record.exc_info:
            record.exc_info = None
            record.exc_text = None
        return True


def _prepare_directory(directory: Path) -> None:
    if directory.exists():
        if not directory.is_dir():
            raise NotADirectoryError(directory)
        return
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)


def _remove_owned_handlers(logger: logging.Logger) -> None:
    removed = False
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()
            removed = True
    if removed:
        logger.setLevel(getattr(logger, _PREVIOUS_LEVEL, logging.NOTSET))
        logger.propagate = getattr(logger, _PREVIOUS_PROPAGATE, True)
        for attribute in (_PREVIOUS_LEVEL, _PREVIOUS_PROPAGATE):
            if hasattr(logger, attribute):
                delattr(logger, attribute)


def configure_diagnostics(
    config: ObservHermesConfig,
    *,
    target_logger: logging.Logger | None = None,
) -> Path | None:
    """Configure the production-only diagnostic sink without affecting telemetry."""
    logger = (
        logging.getLogger("observ_hermes") if target_logger is None else target_logger
    )
    _remove_owned_handlers(logger)
    configured = config.log_dir.strip()
    if not configured:
        return None

    directory = Path(configured).expanduser()
    try:
        _prepare_directory(directory)
        path = directory / LOG_FILE_NAME
        handler = _PrivateRotatingFileHandler(
            path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        setattr(handler, _HANDLER_MARKER, True)
        handler.setLevel(logging.DEBUG if config.debug else logging.INFO)
        handler.addFilter(_SanitizingFilter())
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
        setattr(logger, _PREVIOUS_LEVEL, logger.level)
        setattr(logger, _PREVIOUS_PROPAGATE, logger.propagate)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        return path
    except (OSError, ValueError):
        return None


def close_diagnostics(*, target_logger: logging.Logger | None = None) -> None:
    logger = (
        logging.getLogger("observ_hermes") if target_logger is None else target_logger
    )
    _remove_owned_handlers(logger)

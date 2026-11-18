import logging
import sys
import threading
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Protocol, TextIO

import structlog

_LEVEL_MAP = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

_NAME_TO_FILE: dict[str, str] = {
    "elector": "election.log",
    "heartbeat": "election.log",
    "runtime": "election.log",
    "dispatch": "scheduler.log",
    "schedule-scanner": "scheduler.log",
    "rescue": "scheduler.log",
    "task-cleanup": "scheduler.log",
    "notify": "scheduler.log",
    "runner": "executor.log",
    "custom-rule-worker-pool": "executor.log",
    "signoz.http": "executor.log",
    "ob": "ob.log",
    "inference-proxy": "inference-proxy.log",
}
_DEFAULT_FILE = "executor.log"


def _format_log(_logger: Any, _method: str, event_dict: dict[str, Any]) -> str:
    ts = event_dict.pop("ts", "")
    level = event_dict.pop("level", "info")
    event = event_dict.pop("event", "")
    kv = " ".join(f"{k}={v}" for k, v in event_dict.items())
    return f"{ts} [{level}] {event}  {kv}".rstrip()


class _TeeLogger:
    def __init__(self, targets: list["_LogTarget"]) -> None:
        self._targets = targets
        self._lock = threading.Lock()

    def msg(self, message: str) -> None:
        with self._lock:
            for target in self._targets:
                target.write(message)

    log = debug = info = warn = warning = msg
    fatal = failure = err = error = critical = exception = msg


class _LogTarget(Protocol):
    def write(self, message: str) -> None: ...

    def close(self) -> None: ...


class _StreamTarget:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(self, message: str) -> None:
        print(message, file=self._stream, flush=True)

    def close(self) -> None:
        pass


class _TimedFileTarget:
    def __init__(self, path: Path, retention_days: int) -> None:
        self.handler = TimedRotatingFileHandler(
            path,
            when="midnight",
            interval=1,
            backupCount=retention_days,
            encoding="utf-8",
            delay=True,
            utc=False,
        )
        self.handler.setFormatter(logging.Formatter("%(message)s"))

    def write(self, message: str) -> None:
        record = logging.LogRecord(
            name="agentlens",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        self.handler.handle(record)

    def close(self) -> None:
        self.handler.close()


class _RoutingLoggerFactory:
    def __init__(
        self,
        log_dir: Path,
        enable_stdout: bool,
        retention_days: int,
    ) -> None:
        self._log_dir = log_dir
        self._enable_stdout = enable_stdout
        self._retention_days = retention_days
        self._file_targets: dict[str, _TimedFileTarget] = {}

    def _get_or_open(self, filename: str) -> _TimedFileTarget:
        if filename not in self._file_targets:
            path = self._log_dir / filename
            self._file_targets[filename] = _TimedFileTarget(
                path,
                retention_days=self._retention_days,
            )
        return self._file_targets[filename]

    def __call__(self, *args: Any) -> _TeeLogger:
        name = args[0] if args else None
        filename = _NAME_TO_FILE.get(name, _DEFAULT_FILE) if name else _DEFAULT_FILE
        targets: list[_LogTarget] = [self._get_or_open(filename)]
        if self._enable_stdout:
            targets.append(_StreamTarget(sys.stdout))
        return _TeeLogger(targets)

    def close(self) -> None:
        for target in self._file_targets.values():
            target.close()
        self._file_targets.clear()


_factory: _RoutingLoggerFactory | None = None


def init_logging(
    *,
    instance_uuid: str = "",
    level: str = "INFO",
    log_dir: str | Path = "logs",
    log_stdout: bool = False,
    log_retention_days: int = 7,
    stream: TextIO | None = None,
) -> None:
    global _factory
    structlog.contextvars.clear_contextvars()

    if stream is not None:
        factory: Any = structlog.PrintLoggerFactory(file=stream)
    else:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        _factory = _RoutingLoggerFactory(
            log_dir=log_path,
            enable_stdout=log_stdout,
            retention_days=log_retention_days,
        )
        factory = _factory

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(key="ts", fmt="iso", utc=False),
            _format_log,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_LEVEL_MAP[level]),
        logger_factory=factory,
        cache_logger_on_first_use=False,
    )
    structlog.contextvars.bind_contextvars(instance_uuid=instance_uuid)


def shutdown_logging() -> None:
    global _factory
    if _factory is not None:
        _factory.close()
        _factory = None
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


def get_logger(name: str | None = None) -> structlog.types.FilteringBoundLogger:
    return structlog.get_logger(name)

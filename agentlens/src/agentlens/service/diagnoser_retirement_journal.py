"""Private, atomic local journal; this is not a distributed database transaction."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


class JournalError(ValueError):
    pass


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JournalError("duplicate JSON key")
        result[key] = value
    return result


def read_private_json(path: Path) -> dict:
    """No symlinks, other owners, permissive modes or unbounded input."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > 8 * 1024 * 1024
            ):
                raise JournalError("private JSON must be an owned 0600 regular file <=8 MiB")
            value = json.load(stream, object_pairs_hook=_reject_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JournalError("private JSON is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise JournalError("private JSON must be an object")
    return value


class Journal:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._locked = False

    def read(self) -> dict:
        return read_private_json(self.path)

    @contextmanager
    def locked(self):
        lock_path = self.path.with_name(self.path.name + ".lock")
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            raise JournalError("cannot acquire private manifest lock") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise JournalError("unsafe manifest lock")
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._locked = True
            if self.path.is_symlink():
                raise JournalError("manifest symlink rejected")
            if self.path.exists():
                self.read()
            yield self
        finally:
            self._locked = False
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def write(self, value: dict):
        if not self._locked:
            raise JournalError("manifest must be locked before writing")
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        if len(payload) > 8 * 1024 * 1024:
            raise JournalError("manifest exceeds 8 MiB")
        if self.path.is_symlink():
            raise JournalError("manifest symlink rejected")
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".retirement-journal-", dir=self.path.parent)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise JournalError("manifest could not be persisted; stop before next phase") from exc
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

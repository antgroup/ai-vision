"""Durable, single-use leases and independently verified whole-sandbox cleanup."""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentlens.diagnoser.executors.rule.custom.enhanced_contract import (
    RUNTIME_ID,
    normalize_materials,
    validate_enhanced_code,
    validate_runtime_manifest,
)
from agentlens.diagnoser.executors.rule.custom.sandbox import encode_worker_request
from agentlens.domain.sandbox import (
    INPUT_LIMIT_LABEL,
    LEGACY_INPUT_BYTES,
    MAX_INPUT_BYTES,
    SandboxError,
    input_limit_details,
)
from agentlens.infra.sandbox import encode_request
from agentlens.sandbox_plugin.config import PluginSettings

_MESSAGES = {
    "SANDBOX_INPUT_INVALID": (422, "Sandbox request is invalid"),
    "SANDBOX_NOT_FOUND": (404, "Sandbox lease was not found"),
    "SANDBOX_LEASE_CONFLICT": (409, "Sandbox lease cannot be reused"),
    "SANDBOX_LEASE_BUSY": (409, "Sandbox lease operation is in progress"),
    "SANDBOX_CAPACITY_EXHAUSTED": (429, "Sandbox capacity is unavailable"),
    "SANDBOX_PROVIDER_UNAVAILABLE": (502, "Sandbox provider operation failed"),
    "SANDBOX_UNAUTHORIZED": (401, "Sandbox authentication is required"),
    "SANDBOX_INPUT_TOO_LARGE": (413, "Sandbox request exceeds the input limit"),
    "SANDBOX_INTERNAL_ERROR": (500, "Sandbox operation failed"),
    "SANDBOX_RUNTIME_UNAVAILABLE": (422, "Requested sandbox runtime is unavailable"),
}
_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


class PluginError(RuntimeError):
    def __init__(self, code: str, *, details=None):
        self.details = input_limit_details(details) if code == "SANDBOX_INPUT_TOO_LARGE" else {}
        self.code = code
        self.status_code, self.msg = _MESSAGES[code]
        super().__init__(self.msg)


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lease_id: str = Field(pattern=_ID)
    connection_uuid: str = Field(pattern=_ID)
    space_uuid: str = Field(pattern=_ID)
    task_uuid: str = Field(pattern=_ID)
    attempt: int = Field(ge=0, le=2**31 - 1)
    diagnoser_uuid: str = Field(pattern=_ID)
    image: str = Field(min_length=1, max_length=512)
    cpu: int = Field(ge=1, le=64)
    memory: str = Field(pattern=r"^[1-9][0-9]{0,7}(Mi|Gi)$")
    timeout_seconds: float = Field(gt=0, le=300, allow_inf_nan=False)
    rule_runtime: str | None = None
    rule_runtime_manifest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(min_length=1, max_length=1024**2)
    trace: dict[str, Any]
    materials: dict[str, Any] | None = None
    timeout_seconds: float = Field(gt=0, le=300, allow_inf_nan=False)
    rule_runtime: str | None = None
    rule_runtime_manifest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def _validate(model: type[BaseModel], value: dict) -> Any:
    try:
        return model.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        raise PluginError("SANDBOX_INPUT_INVALID") from None


class LeaseManager:
    """One process per journal; live attempt claims fence the periodic janitor."""

    def __init__(self, settings: PluginSettings, provider: Any, verifier: Any):
        self.settings, self.provider, self.verifier = settings, provider, verifier
        settings.load_policy()
        settings.journal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock_file = os.open(
            str(settings.journal_path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600
        )
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._lock_file)
            raise ValueError("Plugin lease journal already has an active owner") from None
        self._lock = threading.RLock()
        self._live: set[str] = set()
        self._closed = False
        self._draining = False
        descriptor = os.open(settings.journal_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(descriptor)
        self._db = sqlite3.connect(settings.journal_path, check_same_thread=False, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS lease (
            lease_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, request_json TEXT NOT NULL,
            sandbox_name TEXT NOT NULL UNIQUE, sandbox_id TEXT, docker_daemon_id TEXT,
            state TEXT NOT NULL,
            expires_at REAL NOT NULL, cleanup_attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0
        )""")
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(lease)")}
        if "docker_daemon_id" not in columns:
            self._db.execute("ALTER TABLE lease ADD COLUMN docker_daemon_id TEXT")
        self._db.commit()

    def _row(self, lease_id: str) -> dict:
        row = self._db.execute("SELECT * FROM lease WHERE lease_id=?", (lease_id,)).fetchone()
        if row is None:
            raise PluginError("SANDBOX_NOT_FOUND")
        return dict(row)

    def _update(self, lease_id: str, **changes: Any) -> dict:
        # Column names are internal constants, never caller input.
        with self._lock, self._db:
            self._db.execute(
                "UPDATE lease SET " + ",".join(key + "=?" for key in changes) + " WHERE lease_id=?",
                (*changes.values(), lease_id),
            )
            return self._row(lease_id)

    def get(self, lease_id: str) -> dict:
        with self._lock:
            row = self._row(lease_id)
        ownership = json.loads(row["request_json"])
        return {
            key: ownership[key]
            for key in (
                "lease_id",
                "connection_uuid",
                "space_uuid",
                "task_uuid",
                "attempt",
                "diagnoser_uuid",
            )
        } | {
            "sandbox_name": row["sandbox_name"],
            "sandbox_id": row["sandbox_id"],
            "state": row["state"],
            "cleanup_verified": row["state"] == "closed",
        }

    @contextlib.contextmanager
    def _claim(self, lease_id: str):
        with self._lock:
            if lease_id in self._live:
                raise PluginError("SANDBOX_LEASE_BUSY")
            self._live.add(lease_id)
        try:
            yield
        finally:
            with self._lock:
                self._live.discard(lease_id)

    @staticmethod
    def _labels(row: dict) -> dict[str, str]:
        def label(digest: bytes) -> str:
            return base64.b32encode(digest).decode("ascii").lower().rstrip("=")

        return {
            "aivision.lease": label(hashlib.sha256(row["lease_id"].encode()).digest()),
            "aivision.fingerprint": label(bytes.fromhex(row["fingerprint"])),
        }

    def _owned(self, ref: Any, row: dict) -> bool:
        return (
            isinstance(ref.id, str)
            and bool(ref.id)
            and ref.name == row["sandbox_name"]
            and all(ref.labels.get(key) == value for key, value in self._labels(row).items())
            and (not row["sandbox_id"] or ref.id == row["sandbox_id"])
        )

    def input_limit(self, image: str) -> int:
        """Only immutable, image-owned metadata can raise the legacy reader bound."""
        limit = LEGACY_INPUT_BYTES
        if re.search(r"(?:@|^)sha256:[a-f0-9]{64}$", image):
            try:
                metadata = self.verifier.image_metadata(image)
                if image in (metadata.get("RepoDigests") or []) or metadata.get("Id") == image:
                    raw = (metadata.get("Config", {}).get("Labels") or {}).get(INPUT_LIMIT_LABEL)
                    if (
                        isinstance(raw, str)
                        and raw.isdecimal()
                        and 1024 <= int(raw) <= MAX_INPUT_BYTES
                    ):
                        limit = int(raw)
            except Exception:
                pass
        return min(self.settings.max_input_bytes, limit)

    def input_limits(self) -> list[dict]:
        return [
            {"image": image, "max_input_bytes": self.input_limit(image)}
            for image in self.settings.image_allowlist
        ]

    def runtime_manifests(self) -> list[dict]:
        """Read metadata only; never run a sandbox in a health request."""
        image = self.settings.enhanced_image
        if not image:
            return []
        try:
            with self.settings.runtime_manifest_path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                return []
            manifest = validate_runtime_manifest(json.loads(raw))
            metadata = self.verifier.image_metadata(image)
            if not isinstance(metadata, dict):
                return []
            if image not in (metadata.get("RepoDigests") or []) and metadata.get("Id") != image:
                return []
            labels = metadata.get("Config", {}).get("Labels") or {}
            if (
                labels.get("io.aivision.rule-runtime.manifest-sha256")
                != manifest["manifest_sha256"]
            ):
                return []
            return [manifest | {"image": image}]
        except Exception:
            return []

    def _qualify_runtime(self, request: Any, image: str) -> bool:
        if request.rule_runtime is None:
            if request.rule_runtime_manifest is not None:
                raise PluginError("SANDBOX_INPUT_INVALID")
            return False
        if request.rule_runtime != RUNTIME_ID or request.rule_runtime_manifest is None:
            raise PluginError("SANDBOX_RUNTIME_UNAVAILABLE")
        if not any(
            m["image"] == image and m["manifest_sha256"] == request.rule_runtime_manifest
            for m in self.runtime_manifests()
        ):
            raise PluginError("SANDBOX_RUNTIME_UNAVAILABLE")
        return True

    def _create_profile(self, data: dict) -> dict:
        request = _validate(CreateRequest, data)
        self._qualify_runtime(request, request.image)
        value = request.model_dump(exclude_none=True)
        memory_match = re.fullmatch(r"([0-9]+)(Mi|Gi)", request.memory)
        memory = int(memory_match[1]) * (1024**2 if memory_match[2] == "Mi" else 1024**3)
        if (
            request.image not in self.settings.image_allowlist
            or request.cpu > self.settings.max_cpu
            or memory > self.settings.max_memory_bytes
            or request.timeout_seconds > self.settings.max_timeout_seconds
        ):
            raise PluginError("SANDBOX_INPUT_INVALID")
        return value

    def create(self, data: dict) -> dict:
        value = self._create_profile(data)
        request = _validate(CreateRequest, value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        name = "al-" + hashlib.sha256(request.lease_id.encode()).hexdigest()[:16]
        with self._claim(request.lease_id):
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    if self._draining:
                        raise PluginError("SANDBOX_LEASE_CONFLICT")
                    existing = self._db.execute(
                        "SELECT * FROM lease WHERE lease_id=?", (request.lease_id,)
                    ).fetchone()
                    if existing is not None:
                        if existing["fingerprint"] != fingerprint or existing["state"] != "ready":
                            raise PluginError("SANDBOX_LEASE_CONFLICT")
                        self._db.commit()
                        return self._created(dict(existing))
                    count = self._db.execute(
                        "SELECT COUNT(*) FROM lease WHERE state != 'closed'"
                    ).fetchone()[0]
                    if count >= self.settings.max_active:
                        raise PluginError("SANDBOX_CAPACITY_EXHAUSTED")
                    self._db.execute(
                        "INSERT INTO lease (lease_id,fingerprint,request_json,sandbox_name,state,"
                        "expires_at) VALUES (?,?,?,?,?,?)",
                        (
                            request.lease_id,
                            fingerprint,
                            encoded,
                            name,
                            "creating",
                            time.time() + self.settings.lease_ttl_seconds,
                        ),
                    )
                    self._db.commit()
                    row = self._row(request.lease_id)
                except BaseException:
                    self._db.rollback()
                    raise
            try:
                ref = self.provider.create(name, value, self._labels(row))
                if not self._owned(ref, row):
                    raise RuntimeError("Provider ownership was not established")
                row = self._update(request.lease_id, sandbox_id=ref.id)
                ready = self.provider.wait_ready(name, self.settings.ready_timeout_seconds)
                if not self._owned(ready, row):
                    raise RuntimeError("Provider identity changed")
                daemon_id = self.verifier.observe_presence(
                    row["sandbox_id"],
                    row["sandbox_name"],
                    deadline=time.monotonic() + self.settings.operation_timeout_seconds,
                )
                if not isinstance(daemon_id, str) or not daemon_id:
                    raise ValueError("Docker provider identity was not established")
                row = self._update(request.lease_id, state="ready", docker_daemon_id=daemon_id)
                return self._created(row)
            except BaseException as error:
                self._cleanup(request.lease_id)
                if not isinstance(error, Exception):
                    raise
                raise PluginError("SANDBOX_PROVIDER_UNAVAILABLE") from None

    @staticmethod
    def _created(row: dict) -> dict:
        return {
            "lease_id": row["lease_id"],
            "sandbox_id": row["sandbox_id"],
            "state": "ready",
            "cleanup_verified": False,
        }

    def _execution_input(self, request: ExecuteRequest, profile: dict) -> tuple[bytes, list[str]]:
        if request.timeout_seconds > profile["timeout_seconds"]:
            raise PluginError("SANDBOX_INPUT_INVALID")
        enhanced = self._qualify_runtime(request, profile["image"])
        if request.rule_runtime != profile.get(
            "rule_runtime"
        ) or request.rule_runtime_manifest != profile.get("rule_runtime_manifest"):
            raise PluginError("SANDBOX_RUNTIME_UNAVAILABLE")
        if not enhanced and "materials" in request.model_fields_set:
            raise PluginError("SANDBOX_INPUT_INVALID")
        try:
            digest = hashlib.sha256(request.code.encode()).hexdigest()[:32]
            if enhanced:
                validate_enhanced_code(request.code)
                envelope = json.loads(
                    encode_worker_request(digest, request.code, request.trace)
                ) | {
                    "rule_runtime": RUNTIME_ID,
                    "rule_runtime_manifest": request.rule_runtime_manifest,
                    "materials": normalize_materials(request.materials),
                    "timeout_seconds": request.timeout_seconds,
                }
                stdin = (json.dumps(envelope, ensure_ascii=False, allow_nan=False) + "\n").encode()
            else:
                stdin = (encode_worker_request(digest, request.code, request.trace) + "\n").encode()
        except (ValueError, TypeError, RecursionError):
            raise PluginError("SANDBOX_INPUT_INVALID") from None
        limit = self.input_limit(profile["image"])
        if len(stdin) > limit:
            raise PluginError(
                "SANDBOX_INPUT_TOO_LARGE",
                details={
                    "stage": "worker_stdin",
                    "actual_bytes": len(stdin),
                    "allowed_bytes": limit,
                },
            )
        command = (
            ["/app/rule-runtime/enhanced-worker"]
            if enhanced
            else list(self.settings.worker_command)
        )
        return stdin, command

    def validate_execution(self, data: dict) -> dict:
        if not isinstance(data, dict) or set(data) != {"create", "execute"}:
            raise PluginError("SANDBOX_INPUT_INVALID")
        profile = self._create_profile(data["create"])
        request = _validate(ExecuteRequest, data["execute"])
        try:
            encode_request(data, max_bytes=self.input_limit(profile["image"]))
        except SandboxError as exc:
            code = (
                "SANDBOX_INPUT_TOO_LARGE"
                if exc.code == "sandbox_input_limit"
                else "SANDBOX_INPUT_INVALID"
            )
            raise PluginError(code, details=exc.details) from None
        stdin, command = self._execution_input(request, profile)
        name = "al-" + hashlib.sha256(profile["lease_id"].encode()).hexdigest()[:16]
        try:
            transport = self.provider.validate_input(
                name, command, stdin, math.ceil(request.timeout_seconds)
            )
        except SandboxError as exc:
            raise PluginError("SANDBOX_INPUT_TOO_LARGE", details=exc.details) from None
        return {
            "valid": True,
            "input_bytes": len(stdin),
            "max_input_bytes": self.input_limit(profile["image"]),
            **transport,
        }

    def execute(self, lease_id: str, data: dict) -> dict:
        request = _validate(ExecuteRequest, data)
        with self._claim(lease_id):
            with self._lock:
                row = self._row(lease_id)
                profile = json.loads(row["request_json"])
                if row["state"] != "ready" or row["expires_at"] <= time.time():
                    raise PluginError("SANDBOX_LEASE_CONFLICT")
            stdin, command = self._execution_input(request, profile)
            self._update(
                lease_id,
                state="running",
                expires_at=time.time()
                + request.timeout_seconds
                + 10
                + 3 * self.settings.operation_timeout_seconds,
            )
            result = None
            error = None
            try:
                current = self.provider.find(row["sandbox_name"])
                if current is None or not self._owned(current, row):
                    raise RuntimeError("Provider identity changed")
                result = self.provider.execute(
                    row["sandbox_name"],
                    command,
                    stdin,
                    math.ceil(request.timeout_seconds),
                    self.settings.max_output_bytes,
                )
                if type(result.exit_code) is not int:
                    raise ValueError("Provider result is invalid")
                if not isinstance(result.stdout, str) or not isinstance(result.stderr, str):
                    raise ValueError("Provider result is invalid")
            except BaseException as exc:
                error = exc
            finally:
                cleanup = self._cleanup(lease_id)
            if error is not None:
                if isinstance(error, SandboxError) and error.code == "sandbox_input_limit":
                    raise PluginError("SANDBOX_INPUT_TOO_LARGE", details=error.details) from None
                if not isinstance(error, Exception):
                    raise error
                raise PluginError("SANDBOX_PROVIDER_UNAVAILABLE") from None
            stdout = result.stdout.encode("utf-8")
            stderr = result.stderr.encode("utf-8")
            limit = self.settings.max_output_bytes
            truncated = (
                bool(getattr(result, "truncated", False)) or len(stdout) + len(stderr) > limit
            )
            bounded_stdout = stdout[:limit]
            bounded_stderr = stderr[: max(0, limit - len(bounded_stdout))]
            return {
                "exit_code": result.exit_code,
                "stdout": bounded_stdout.decode("utf-8", errors="ignore"),
                "stderr": bounded_stderr.decode("utf-8", errors="ignore"),
                "timed_out": result.exit_code == 124,
                "truncated": truncated,
                "cleanup_verified": cleanup["cleanup_verified"],
            }

    def delete(self, lease_id: str) -> dict:
        with self._claim(lease_id):
            return self._cleanup(lease_id)

    def _cleanup(self, lease_id: str) -> dict:
        with self._lock:
            row = self._row(lease_id)
        if row["state"] == "closed":
            return {"lease_id": lease_id, "state": "closed", "cleanup_verified": True}
        row = self._update(lease_id, state="cleanup_pending")
        verified = False
        deadline = time.monotonic() + self.settings.cleanup_timeout_seconds
        budget = getattr(
            self.provider, "cleanup_budget", lambda _deadline: contextlib.nullcontext()
        )
        try:
            with budget(deadline):
                current = self.provider.find(row["sandbox_name"])
                if current is not None:
                    if not self._owned(current, row):
                        raise ValueError("Provider ownership was not established")
                    if not row["sandbox_id"]:
                        row = self._update(lease_id, sandbox_id=current.id)
                # A lost create response may arrive later. An unknown immutable ID keeps its slot.
                if not row["sandbox_id"]:
                    raise ValueError("Provider immutable identity is unknown")
                if not row["docker_daemon_id"]:
                    # Establish readback identity while the owned resources are still
                    # observable. Failure must not prevent the SDK teardown attempt.
                    try:
                        daemon_id = self.verifier.observe_presence(
                            row["sandbox_id"],
                            row["sandbox_name"],
                            deadline=min(
                                deadline, time.monotonic() + self.settings.operation_timeout_seconds
                            ),
                        )
                        if isinstance(daemon_id, str) and daemon_id:
                            row = self._update(lease_id, docker_daemon_id=daemon_id)
                    except Exception:
                        pass
                self.provider.delete(row["sandbox_name"], row["sandbox_id"])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                self.provider.wait_deleted(
                    row["sandbox_name"],
                    row["sandbox_id"],
                    remaining,
                    labels=self._labels(row),
                )
                if deadline <= time.monotonic():
                    raise TimeoutError
                verified = (
                    bool(row["docker_daemon_id"])
                    and self.verifier.verify_absent(
                        row["sandbox_id"],
                        row["sandbox_name"],
                        daemon_id=row["docker_daemon_id"],
                        deadline=deadline,
                    )
                    is True
                )
                if deadline <= time.monotonic():
                    verified = False
        except Exception:
            # No provider details or user payloads are logged or written to the journal.
            verified = False
        attempts = row["cleanup_attempts"] + 1
        state = "closed" if verified else "cleanup_pending"
        self._update(
            lease_id,
            state=state,
            cleanup_attempts=attempts,
            retry_at=time.time() + min(300, 2 ** min(attempts, 8)),
        )
        return {"lease_id": lease_id, "state": state, "cleanup_verified": verified}

    def recover_startup(self) -> None:
        # The exclusive journal lock proves no other process owns these attempts.
        with self._lock:
            ids = [
                row[0]
                for row in self._db.execute("SELECT lease_id FROM lease WHERE state!='closed'")
            ]
        for lease_id in ids:
            with self._claim(lease_id):
                self._cleanup(lease_id)

    def janitor_once(self, *, due_only: bool = False) -> None:
        now = time.time()
        with self._lock:
            rows = self._db.execute(
                "SELECT lease_id,state,expires_at,retry_at FROM lease WHERE state!='closed'"
            ).fetchall()
        for row in rows:
            if row["state"] != "cleanup_pending" and row["expires_at"] > now:
                continue
            if due_only and row["retry_at"] > now:
                continue
            try:
                with self._claim(row["lease_id"]):
                    self._cleanup(row["lease_id"])
            except PluginError:
                continue

    def lease_status(self) -> dict:
        with self._lock:
            count = self._db.execute("SELECT COUNT(*) FROM lease WHERE state!='closed'").fetchone()[
                0
            ]
            return {"active_leases": count, "draining": self._draining}

    def drain(self) -> dict:
        with self._lock:
            self._draining = True
            return self.lease_status()

    def ready(self) -> bool:
        if self.lease_status()["draining"]:
            return False
        try:
            return bool(self.provider.ready() and self.verifier.ready())
        except Exception:
            return False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._db.close()
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            os.close(self._lock_file)

"""Persistent Custom Rule Worker pool with subprocess isolation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from threading import Lock
from typing import Any

from agentlens.diagnoser.executors.rule.custom.sandbox import (
    SandboxError,
    encode_worker_request,
    execute_one_shot,
    worker_command,
)
from agentlens.diagnoser.executors.rule.custom.validator import validate_code
from agentlens.logging import get_logger

_logger = get_logger("custom-rule-worker-pool")


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class _Worker:
    """A single persistent Worker subprocess."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._io_lock = Lock()

    def start(self) -> None:
        self._proc = subprocess.Popen(
            worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()
                self._proc.wait(timeout=1)
            finally:
                self._proc = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def execute(
        self,
        rule_code_hash: str,
        code: str,
        trace_dict: dict,
        exec_timeout: float,
    ) -> tuple[dict, bool]:
        with self._io_lock:
            if not self.alive:
                raise SandboxError("Worker not alive", failure_code="rule_sandbox_error")
            req = encode_worker_request(rule_code_hash, code, trace_dict) + "\n"
            try:
                assert self._proc is not None
                assert self._proc.stdin is not None
                assert self._proc.stdout is not None
                self._proc.stdin.write(req)
                self._proc.stdin.flush()

                import select

                ready, _, _ = select.select([self._proc.stdout], [], [], exec_timeout)
                if not ready:
                    self.stop()
                    raise SandboxError(
                        f"Worker 执行超时 ({exec_timeout}s)",
                        failure_code="exec_timeout",
                    )
                line = self._proc.stdout.readline()
                if not line:
                    self.stop()
                    raise SandboxError(
                        "Worker 进程异常退出",
                        failure_code="rule_sandbox_error",
                    )
                resp = json.loads(line)
                if resp.get("status") == "error":
                    raise SandboxError(
                        resp.get("reason", "unknown error"),
                        failure_code=resp.get("failure_code", "rule_runtime_error"),
                    )
                return resp.get("result", {}), bool(resp.get("cache_hit"))
            except SandboxError:
                raise
            except Exception as exc:
                self.stop()
                raise SandboxError(
                    f"Worker 通信错误: {exc}",
                    failure_code="rule_sandbox_error",
                ) from exc


class CustomRuleWorkerPool:
    """Fixed-size pool of persistent Worker subprocesses."""

    def __init__(self, size: int = 3) -> None:
        self._size = max(1, size)
        self._workers: list[_Worker] = []
        self._lock = Lock()
        self._index = 0
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._workers = [_Worker() for _ in range(self._size)]
            for w in self._workers:
                w.start()
            self._started = True
            _logger.info("pool.started", size=self._size)

    def stop(self) -> None:
        with self._lock:
            for w in self._workers:
                w.stop()
            self._workers.clear()
            self._started = False
            _logger.info("pool.stopped")

    def execute(
        self,
        rule_code_hash: str,
        code: str,
        trace_dict: dict[str, Any],
        exec_timeout: float = 5.0,
    ) -> tuple[dict[str, Any], bool]:
        with self._lock:
            if not self._started or not self._workers:
                _logger.info("pool.fallback_one_shot")
                compiled_code = validate_code(code)
                result = execute_one_shot(
                    compiled_code,
                    trace_dict,
                    timeout_seconds=exec_timeout,
                    code=code,
                )
                return result, False
            worker = self._workers[self._index % len(self._workers)]
            self._index += 1

        try:
            if not worker.alive:
                self._replace_worker(worker)
                with self._lock:
                    worker = self._workers[(self._index - 1) % len(self._workers)]
            return worker.execute(rule_code_hash, code, trace_dict, exec_timeout)
        except SandboxError as exc:
            if exc.failure_code in ("exec_timeout", "rule_sandbox_error"):
                self._replace_worker(worker)
            raise

    def _replace_worker(self, old: _Worker) -> None:
        with self._lock:
            try:
                idx = self._workers.index(old)
            except ValueError:
                return
            old.stop()
            new = _Worker()
            new.start()
            self._workers[idx] = new
            _logger.info("pool.worker_replaced", index=idx)


_custom_rule_worker_pool: CustomRuleWorkerPool | None = None


def set_custom_rule_worker_pool(pool: CustomRuleWorkerPool | None) -> None:
    global _custom_rule_worker_pool
    _custom_rule_worker_pool = pool


def get_custom_rule_worker_pool() -> CustomRuleWorkerPool | None:
    return _custom_rule_worker_pool

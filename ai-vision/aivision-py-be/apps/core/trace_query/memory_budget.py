"""Per-process admission and per-request byte budgets for native trace reads.

Admission is lazy: ordinary control-plane requests never take a slot. The slot
is held through rendering and WSGI response close, including slow consumers.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from threading import Lock

from django.conf import settings

from .types import TraceQueryServiceError

_current = ContextVar('trace_query_budget', default=None)
_lock = Lock()
_active = 0


def positive_setting(name, default):
    value = int(getattr(settings, name, default))
    if value <= 0:
        raise ValueError(f'{name} must be positive')
    return value


class QueryMemoryBudget:
    def __init__(self, *, max_bytes=None):
        self.max_bytes = max_bytes
        self.admitted = False
        self.bytes_read = 0
        self.lock = Lock()

    def admit(self):
        global _active
        with self.lock, _lock:
            if self.admitted:
                return
            if _active >= positive_setting('AIVISION_TRACE_QUERY_CONCURRENCY', 2):
                raise TraceQueryServiceError('Trace query capacity is busy; retry shortly', 3037)
            _active += 1
            self.admitted = True

    def consume(self, size):
        with self.lock:
            self.bytes_read += size
            limit = self.max_bytes if self.max_bytes is not None else positive_setting('AIVISION_TRACE_REQUEST_MAX_BYTES', 64 * 1024 * 1024)
            if self.bytes_read > limit:
                raise TraceQueryServiceError('Trace request exceeds the byte budget; narrow the time range or page size', 3036)

    def close(self):
        global _active
        with self.lock, _lock:
            if self.admitted:
                _active -= 1
                self.admitted = False


def admit_query():
    budget = _current.get()
    if budget is not None:
        budget.admit()


def consume_bytes(size):
    budget = _current.get()
    if budget is not None:
        budget.consume(size)


class TraceQueryMemoryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        budget = QueryMemoryBudget()
        token = _current.set(budget)
        try:
            response = self.get_response(request)
            # Django invokes resource closers for normal completion, client
            # disconnects and server-side request cleanup.
            response._resource_closers.append(budget.close)
            return response
        except BaseException:
            # Cancellation can bypass Exception before a response owns cleanup.
            budget.close()
            raise
        finally:
            _current.reset(token)


@contextmanager
def query_memory_scope(*, max_bytes=None):
    """Explicit budget for a background source batch; preserve HTTP parents."""
    if _current.get() is not None:
        yield
        return
    budget = QueryMemoryBudget(max_bytes=max_bytes)
    token = _current.set(budget)
    try:
        budget.admit()
        yield
    finally:
        budget.close()
        _current.reset(token)

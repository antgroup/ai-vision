"""Share one active, authorized metrics snapshot; retain no completed cache.

Consumers only read the completed canonical window. The initiating request runs
the read with its normal storage deadline; no extra worker or Django connection
is created, and a disconnected browser does not cancel another section's read.
"""
from concurrent.futures import Future
import hashlib
import json
from threading import Lock

_lock = Lock()
_active = {}


def scope_key(client, wire):
    value = ['native-trace-metrics-window-v1', str(client.endpoint), str(client.api_key), wire]
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_once(key, read):
    with _lock:
        pending = _active.get(key)
        owner = pending is None
        if owner:
            pending = _active[key] = Future()
    if not owner:
        return pending.result()
    try:
        result = read()
        pending.set_result(result)
        return result
    except BaseException as exc:
        pending.set_exception(exc)
        raise
    finally:
        with _lock:
            if _active.get(key) is pending:
                del _active[key]

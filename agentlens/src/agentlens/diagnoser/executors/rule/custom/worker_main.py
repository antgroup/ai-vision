#!/usr/bin/env python3
"""Persistent Custom Rule Worker, run with `python -I -S worker_main.py`.

Protocol: one JSON line in → one JSON line out, over stdin/stdout.
Each line is a request/response pair. The Worker caches compiled code by
code_hash (LRU, max 64 entries) and executes evaluate(trace) in a fresh
local scope per request.
"""

import collections
import json
import sys
import traceback

_MAX_CACHE_SIZE = 64

_SAFE_BUILTINS = {
    "True": True,
    "False": False,
    "None": None,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "sorted": sorted,
    "reversed": reversed,
    "filter": filter,
    "map": map,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "isinstance": isinstance,
    "type": type,
    "hasattr": hasattr,
    "any": any,
    "all": all,
    "print": lambda *_a, **_kw: None,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
    "ZeroDivisionError": ZeroDivisionError,
    "NameError": NameError,
    "LookupError": LookupError,
    "ArithmeticError": ArithmeticError,
    "OverflowError": OverflowError,
    "Exception": Exception,
}

_code_cache: collections.OrderedDict[str, object] = collections.OrderedDict()


def _get_code(rule_code_hash: str, code: str) -> object:
    cached = _code_cache.get(rule_code_hash)
    if cached is not None:
        _code_cache.move_to_end(rule_code_hash)
        return cached
    compiled_code = compile(code, "<custom_rule>", "exec")
    _code_cache[rule_code_hash] = compiled_code
    while len(_code_cache) > _MAX_CACHE_SIZE:
        _code_cache.popitem(last=False)
    return compiled_code


class _JsonFacade:
    """Restricted json API: only loads/dumps, no file operations."""

    loads = staticmethod(json.loads)
    dumps = staticmethod(json.dumps)
    JSONDecodeError = json.JSONDecodeError


class _ReFacade:
    """Restricted re API: compile/search/match/findall/sub/split only."""

    import re as _re

    compile = staticmethod(_re.compile)
    search = staticmethod(_re.search)
    match = staticmethod(_re.match)
    fullmatch = staticmethod(_re.fullmatch)
    findall = staticmethod(_re.findall)
    finditer = staticmethod(_re.finditer)
    sub = staticmethod(_re.sub)
    subn = staticmethod(_re.subn)
    split = staticmethod(_re.split)
    escape = staticmethod(_re.escape)
    IGNORECASE = _re.IGNORECASE
    MULTILINE = _re.MULTILINE
    DOTALL = _re.DOTALL
    VERBOSE = _re.VERBOSE
    del _re


class _MathFacade:
    """Restricted math API: common functions, no OS interaction."""

    import math as _m

    ceil = staticmethod(_m.ceil)
    floor = staticmethod(_m.floor)
    sqrt = staticmethod(_m.sqrt)
    log = staticmethod(_m.log)
    log10 = staticmethod(_m.log10)
    log2 = staticmethod(_m.log2)
    pow = staticmethod(_m.pow)
    exp = staticmethod(_m.exp)
    fabs = staticmethod(_m.fabs)
    isfinite = staticmethod(_m.isfinite)
    isinf = staticmethod(_m.isinf)
    isnan = staticmethod(_m.isnan)
    pi = _m.pi
    e = _m.e
    inf = _m.inf
    nan = _m.nan
    del _m


_json_facade = _JsonFacade()
_re_facade = _ReFacade()
_math_facade = _MathFacade()


def _execute(code: object, trace: dict) -> dict:
    restricted: dict = {"__builtins__": dict(_SAFE_BUILTINS)}
    restricted["json"] = _json_facade
    restricted["re"] = _re_facade
    restricted["math"] = _math_facade
    exec(code, restricted)  # type: ignore[arg-type]
    evaluate_fn = restricted.get("evaluate")
    if not callable(evaluate_fn):
        return {
            "status": "error",
            "failure_code": "rule_sandbox_error",
            "reason": "evaluate function not defined",
        }
    return evaluate_fn(trace)


def main(*, max_input_bytes: int | None = None) -> None:
    # The historical local protocol keeps its existing reader behavior.
    size = -1 if max_input_bytes is None else max_input_bytes + 1
    for line in iter(lambda: sys.stdin.buffer.readline(size), b""):
        if max_input_bytes is not None and len(line) > max_input_bytes:
            sys.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "failure_code": "rule_runtime_error",
                        "reason": "worker input exceeds bound",
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            return
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            rule_code_hash = req["code_hash"]
            code = req["code"]
            trace = req["trace"]
            cache_hit = rule_code_hash in _code_cache
            compiled_code = _get_code(rule_code_hash, code)
            result = _execute(compiled_code, trace)
            resp = {"status": "ok", "result": result, "cache_hit": cache_hit}
        except Exception:
            resp = {
                "status": "error",
                "failure_code": "rule_runtime_error",
                "reason": traceback.format_exc(limit=3),
            }
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def sandbox_main() -> None:
    """The optional remote image has a separately qualified bounded reader."""
    main(max_input_bytes=16 * 1024**2)


if __name__ == "__main__":
    main()

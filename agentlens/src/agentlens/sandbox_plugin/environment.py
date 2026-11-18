"""Read-only host prerequisites for pinned OpenShell; no workload is created."""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re

import httpx

_ENGINE = re.compile(r"([0-9]+(?:\.[0-9]+){1,3})(?:[-+][0-9A-Za-z.-]+)?\Z")


def _local_capability() -> tuple[str, int]:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "aarch64"}:
        return "", 0
    # landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION) is
    # read-only and reports the active ABI, including distribution backports.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(ctypes.c_long(444), ctypes.c_void_p(), ctypes.c_size_t(0), ctypes.c_uint(1))
    return os.uname().release, max(0, int(abi))


def _daemon_info(socket: str) -> dict:
    transport = httpx.HTTPTransport(uds=socket)
    with httpx.Client(transport=transport, trust_env=False, timeout=2) as client:
        with client.stream("GET", "http://docker/info") as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 1024 * 1024:
                    raise ValueError("Docker metadata exceeds probe budget")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("Docker metadata is invalid")
    return data


def probe_environment(socket: str) -> dict:
    """Return only numeric public facts and allowlisted errors, never raw output.

    `passed` verifies host prerequisites only. Authenticated service readiness and
    actual sandbox execution/cleanup are separate checks.
    """
    result = {"status": "failed"}

    def failure(code, actual=None, required=None):
        diagnostic = {"code": code}
        if actual is not None:
            diagnostic["actual"] = str(actual)
        if required is not None:
            diagnostic["required"] = str(required)
        return {**result, "diagnostic": diagnostic}

    try:
        info = _daemon_info(socket)
        version = info.get("ServerVersion")
        match = _ENGINE.fullmatch(version) if isinstance(version, str) else None
        if not match or len(match[1]) > 64 or info.get("OSType") != "linux":
            return failure("environment_probe_failed")
        result["docker_version"] = match[1]
        if int(match[1].split(".")[0]) < 28:
            return failure("docker_version_unsupported", match[1], "28.0")
        kernel, abi = _local_capability()
        if not kernel or kernel != info.get("KernelVersion"):
            return failure("environment_probe_failed")
        result["landlock_abi"] = abi
        if abi <= 0:
            return failure("landlock_unavailable", 0, 3)
        if abi < 3:
            return failure("landlock_abi_unsupported", abi, 3)
        return {**result, "status": "passed"}
    except Exception:
        return failure("environment_probe_failed")

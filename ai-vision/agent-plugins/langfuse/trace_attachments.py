"""Explicit opt-in capture of a file already selected by an Agent integration.

This helper never scans directories or reads paths found in untrusted telemetry.
The caller must supply an absolute path and an explicit allowed root.
"""
from __future__ import annotations

import mimetypes
import os
import stat
from pathlib import Path

import httpx

MAX_BYTES = 16 * 1024 * 1024


def upload_trace_file(*, path, allowed_root, trace_id, span_id, direction, base_url, public_key, secret_key, mime_type=None, max_bytes=MAX_BYTES):
    root_input = Path(allowed_root)
    selected = Path(path)
    if not root_input.is_absolute() or not selected.is_absolute() or not selected.is_relative_to(root_input):
        raise ValueError("file must be an absolute path under the allowed root")
    # Walk via directory descriptors; do not follow symlinks in any component.
    relative = selected.relative_to(root_input)
    root = root_input.resolve(strict=True)
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("invalid file path")
    descriptors = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in relative.parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
        file_fd = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        descriptors.append(file_fd)
        info = os.fstat(file_fd)
        if max_bytes <= 0 or not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > max_bytes:
            raise ValueError("file must be regular and within the attachment size limit")
        with os.fdopen(os.dup(file_fd), "rb") as stream:
            content = stream.read(max_bytes + 1)
        if len(content) != info.st_size or len(content) > max_bytes:
            raise ValueError("file changed during capture")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    mime = mime_type or mimetypes.guess_type(selected.name)[0] or "application/octet-stream"
    response = httpx.post(
        base_url.rstrip("/") + "/api/public/trace-attachments",
        auth=(public_key, secret_key),
        data={"trace_id": trace_id, "span_id": span_id, "direction": direction},
        files={"file": (selected.name, content, mime)},
        timeout=30,
        trust_env=False,
    )
    response.raise_for_status()
    return response.json()

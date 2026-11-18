"""Expose owned host fixtures on a Docker VM's loopback via scoped SSH forwards."""

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit

from codex_provider import loopback


@contextmanager
def docker_loopback(*urls):
    config = os.environ.get("AIVISION_E2E_DOCKER_SSH_CONFIG")
    if not config:
        yield urls
        return
    config = Path(config).resolve(strict=True)
    parsed = [urlsplit(loopback(url)) for url in urls]
    with tempfile.TemporaryDirectory(prefix="aiv-ssh-") as directory:
        command = [
            "ssh",
            "-F",
            str(config),
            "-S",
            str(Path(directory) / "control"),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ExitOnForwardFailure=yes",
        ]
        subprocess.run(
            command + ["-M", "-N", "-f", "colima"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        try:
            endpoints = []
            for url in parsed:
                host = "[::1]" if url.hostname == "::1" else url.hostname
                result = subprocess.run(
                    command
                    + [
                        "-O",
                        "forward",
                        "-R",
                        f"127.0.0.1:0:{host}:{url.port or 80}",
                        "colima",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                port = int(result.stdout.strip())
                if not 0 < port < 65536:
                    raise ValueError("SSH did not allocate a valid loopback port")
                endpoints.append(
                    urlunsplit(("http", f"127.0.0.1:{port}", url.path, "", ""))
                )
            yield tuple(endpoints)
        finally:
            primary = sys.exception()
            try:
                subprocess.run(
                    command + ["-O", "exit", "colima"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except BaseException as cleanup:
                if primary is None:
                    raise
                cleanup_description = f"{type(cleanup).__name__}: {cleanup}"
                if not isinstance(primary, Exception):
                    primary.add_note(
                        "Docker loopback cleanup failed: " + cleanup_description
                    )
                    raise primary from cleanup
                raise BaseExceptionGroup(
                    f"Docker loopback invocation and cleanup failed: {type(primary).__name__}: {primary}; {cleanup_description}",
                    [primary, cleanup],
                ) from None

"""Describe a failed packaged deployment without emitting any runner log text."""

import argparse
import json
from pathlib import Path


COMPONENTS = ("database", "data-backend", "ingest-gateway", "backend", "diagnosis", "frontend")
FAILURES = {
    "port_conflict": ("port is already allocated", "address already in use", "failed to bind"),
    "registry": ("failed to pull", "pull access denied", "toomanyrequests", "manifest unknown"),
    "network": ("tls handshake timeout", "connection reset", "connection refused", "i/o timeout"),
    "disk_capacity": ("no space left on device", "disk full"),
    "memory_capacity": ("out of memory", "cannot allocate memory", "oomkilled=true"),
    "database_auth": ("access denied for user", "error 1045"),
    "health_probe_exhausted": ("not healthy after", "health check timed out"),
    "graceful_stop_failed": ("did not stop within", "failed to stop cleanly", "wait for database shutdown"),
    "unexpected_container_exit": ("stopped with status=", "is restarting or dead"),
    "deadline": ("deadline exceeded", "timed out", "timeout expired"),
}


def classify_lifecycle_log(log):
    component = None
    phase = None
    for line in log.splitlines():
        line = line.strip()
        for name in COMPONENTS:
            if line == f"==> start {name} (container)":
                component, phase = name, "start"
                break
            if line.startswith(f"waiting for {name} health"):
                component, phase = name, "health"
                break
        if line.startswith("[prestart] "):
            phase = "prestart"
        elif line.startswith("[bootstrap] "):
            phase = "bootstrap"
        elif line.startswith("ok deploy complete"):
            phase = "complete"
    normalized = log.lower()
    return {
        "last_observed_component": component,
        "last_observed_deploy_phase": phase,
        "observations": [
            category for category, messages in FAILURES.items()
            if any(message in normalized for message in messages)
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    evidence = {}
    for step in ("cast", "recast", "destroy"):
        path = args.directory / f"{step}.log"
        if path.is_file():
            evidence[step] = classify_lifecycle_log(path.read_text(encoding="utf-8", errors="replace"))
    print(json.dumps({"release_smoke_failure": evidence}, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run backend-owned integration commands; export only the non-secret org id."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def command(*args, capture=False, deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + 900
    for attempt in range(12):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("backend initialization exceeded its 900s deadline")
        try:
            launcher = ["/app/manage"] if os.access("/app/manage", os.X_OK) else [sys.executable, "manage.py"]
            result = subprocess.run([*launcher, *args], cwd="/app",
                                    text=True, stdout=subprocess.PIPE if capture else None,
                                    timeout=min(180, remaining))
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and result.returncode == 0:
            return result.stdout.strip() if capture else ""
        if attempt < 11:
            time.sleep(max(0, min(5, deadline - time.monotonic())))
    raise RuntimeError(f"backend command {args[0]} failed")


def main():
    deadline = time.monotonic() + 900
    command("configure_third_party_agents", "--endpoint", os.environ["SIGNOZ_INTERNAL_URL"], deadline=deadline)
    command("bootstrap_signoz_integration", deadline=deadline)
    org = command("signoz_integration_status", "--require", "--print-org", capture=True, deadline=deadline)
    if not org or len(org.splitlines()) != 1 or any(c.isspace() for c in org):
        raise RuntimeError("backend did not return a valid organization identity")
    destination = Path(os.environ.get("AIV_ORG_OUTPUT", "/run/aivision/org-id"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".org-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(org + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)  # Contains only a non-secret organization identifier.
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    main()

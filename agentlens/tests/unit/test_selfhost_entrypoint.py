"""Exercise the image entrypoint without starting databases or Uvicorn."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).parents[2] / "docker" / "selfhost-entrypoint.sh"


def run_entrypoint(tmp_path, *, org=None, org_file=None, native=False, default_command=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A symlink in another bin directory loses the virtualenv's pyvenv.cfg.
    (bin_dir / "python").write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    (bin_dir / "python").chmod(0o755)
    if default_command:
        (bin_dir / "python").write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" > server-command\n")
        (bin_dir / "python").chmod(0o755)
    alembic = bin_dir / "alembic"
    alembic.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" > migrations\n")
    alembic.chmod(0o755)
    native_migrate = bin_dir / "agentlens-migrate"
    if native:
        native_migrate.write_text(
            '#!/bin/sh\nif [ "$1" = read-org-id-file ]; then\n'
            '  cat "$SIGNOZ_ORG_ID_FILE"\n  exit\nfi\n'
            "printf '%s\\n' \"$*\" > native-migrations\n"
        )
        native_migrate.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    env.pop("SIGNOZ_ORG_ID", None)
    env.pop("SIGNOZ_ORG_ID_FILE", None)
    if org is not None:
        env["SIGNOZ_ORG_ID"] = org
    if org_file is not None:
        env["SIGNOZ_ORG_ID_FILE"] = str(org_file)
    # Only relocate the container's fixed application directory for this host test.
    script = ENTRYPOINT.read_text().replace("cd /app", f"cd {shlex.quote(str(tmp_path))}")
    script = script.replace("/app/agentlens-migrate", str(native_migrate))
    command = (
        ["bash", "-s"]
        if default_command
        else [
            "bash",
            "-s",
            "--",
            sys.executable,
            "-c",
            "import os; print(os.environ.get('SIGNOZ_ORG_ID', ''))",
        ]
    )
    return subprocess.run(
        command,
        input=script,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_file_exports_org_before_migration_and_command(tmp_path):
    org_file = tmp_path / "org"
    org_file.write_text("produced-org-123\n")
    result = run_entrypoint(tmp_path, org_file=org_file)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "produced-org-123"
    assert (tmp_path / "migrations").read_text().strip() == "-c obschema/alembic.ini upgrade head"


def test_explicit_org_takes_precedence_over_missing_file(tmp_path):
    result = run_entrypoint(tmp_path, org="explicit-org", org_file=tmp_path / "absent")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "explicit-org"


@pytest.mark.parametrize("content", [None, "", " \n", "first-org\nsecond-org"])
def test_invalid_org_file_fails_before_migration(tmp_path, content):
    org_file = tmp_path / "org"
    if content is not None:
        org_file.write_text(content)
    result = run_entrypoint(tmp_path, org_file=org_file)
    assert result.returncode != 0
    assert "SIGNOZ_ORG_ID_FILE" in result.stderr
    assert not (tmp_path / "migrations").exists()


def test_native_entrypoint_uses_native_bootstrap_and_migrations(tmp_path):
    org_file = tmp_path / "org"
    org_file.write_text("native-org\n")
    result = run_entrypoint(tmp_path, org_file=org_file, native=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "native-org"
    assert (tmp_path / "native-migrations").read_text().strip() == (
        "-c obschema/alembic.ini upgrade head"
    )
    assert not (tmp_path / "migrations").exists()


def test_default_server_uses_relocated_environment_interpreter(tmp_path):
    result = run_entrypoint(tmp_path, org="native-org", native=True, default_command=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "server-command").read_text().split()[:2] == ["-m", "uvicorn"]

"""Portable executable fixtures; only Windows' script loader is substituted."""
from contextlib import contextmanager
from pathlib import Path
import shlex
import subprocess
import sys
from unittest.mock import patch

WINDOWS = sys.platform == 'win32'


@contextmanager
def executable_fixture(path: Path, source: str):
    # A shebang cannot quote an interpreter path containing spaces. On POSIX,
    # let the shell exec the real interpreter with shell-quoted arguments.
    content = source if WINDOWS else '#!/bin/sh\nexec ' + shlex.join([sys.executable, '-c', source]) + ' "$@"\n'
    path.write_text(content, encoding='utf-8')
    path.chmod(0o700)
    if not WINDOWS:
        yield path
        return

    # CreateProcess does not interpret shebangs. Keep the real child process,
    # stdin, environment, output and exit status, replacing just this loader.
    run = subprocess.run

    def launch(command, *args, **kwargs):
        if command[0] in (str(path), str(path.resolve())):
            command = [sys.executable, str(path), *command[1:]]
        return run(command, *args, **kwargs)

    with patch.object(subprocess, 'run', side_effect=launch):
        yield path

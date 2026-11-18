"""Test-only liveness checks: an unreaped zombie has already stopped executing."""
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def _read_state(pid):
    if sys.platform == 'linux':
        try:
            status = Path(f'/proc/{pid}/status').read_text()
        except (FileNotFoundError, ProcessLookupError):
            # Reaping after open but before read can produce ESRCH, not ENOENT.
            # The caller still confirms absence with an independent kill(0).
            raise ProcessLookupError from None
        except OSError as error:
            number = error.errno if type(error.errno) is int else 'unknown'
            raise AssertionError(f'Cannot read process state (errno={number})') from None
        states = [line.split()[1:] for line in status.splitlines() if line.startswith('State:')]
        if len(states) != 1 or not states[0] or states[0][0] not in tuple('RSDZTWtXxKIP'):
            raise AssertionError('Unknown process state')
        return states[0][0]
    try:
        result = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)],
            capture_output=True, text=True, timeout=.5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise AssertionError('Cannot query process state') from None
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        raise ProcessLookupError
    state = re.fullmatch(r'([RSDZTWtXxKIPU])[<NLsl+]*', result.stdout.strip())
    if result.returncode or result.stderr.strip() or not state:
        raise AssertionError('Cannot verify process state')
    return state[1]


def assert_posix_process_stopped(pid, *, timeout=0):
    if type(pid) is not int or pid <= 0:
        raise AssertionError('A positive process ID is required')
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except OSError:
            raise AssertionError('Cannot inspect process liveness') from None
        try:
            state = _read_state(pid)
        except ProcessLookupError:
            # It may have been reaped between kill(0) and the state lookup.
            # Missing metadata alone is never evidence of successful cleanup.
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except OSError:
                raise AssertionError('Cannot inspect process liveness') from None
            raise AssertionError('Process still exists but its state is unavailable') from None
        if state == 'Z':
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError('Owned process is still live')
        time.sleep(min(.01, remaining))

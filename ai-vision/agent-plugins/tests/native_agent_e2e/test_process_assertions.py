"""The cleanup oracle distinguishes executing processes from unreaped zombies."""
import errno
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import _process_test_helpers as process_state


class ProcessStateTests(unittest.TestCase):
    def test_missing_pid_and_confirmed_lookup_race_are_accepted(self):
        with patch.object(process_state.os, 'kill', side_effect=ProcessLookupError), \
                patch.object(process_state, '_read_state') as read:
            process_state.assert_posix_process_stopped(123)
            read.assert_not_called()
        with patch.object(process_state.os, 'kill', side_effect=[None, ProcessLookupError]), \
                patch.object(process_state, '_read_state', side_effect=ProcessLookupError):
            process_state.assert_posix_process_stopped(123)

    def test_missing_state_does_not_accept_a_pid_that_still_exists(self):
        with patch.object(process_state.os, 'kill'), \
                patch.object(process_state, '_read_state', side_effect=ProcessLookupError):
            with self.assertRaises(AssertionError):
                process_state.assert_posix_process_stopped(123)

    def test_linux_disappeared_state_requires_a_second_missing_pid_check(self):
        for error in (FileNotFoundError(errno.ENOENT, 'controlled missing path'),
                      ProcessLookupError(errno.ESRCH, 'controlled reaped process')):
            with self.subTest(errno=error.errno), patch.object(process_state.sys, 'platform', 'linux'), \
                    patch.object(Path, 'read_text', side_effect=error):
                with patch.object(process_state.os, 'kill', side_effect=[None, ProcessLookupError]):
                    process_state.assert_posix_process_stopped(123)
                with patch.object(process_state.os, 'kill'):
                    with self.assertRaises(AssertionError):
                        process_state.assert_posix_process_stopped(123)

    def test_live_states_and_unreadable_state_fail_closed(self):
        for state in ('R', 'S', 'T', 'D'):
            with self.subTest(state=state), patch.object(process_state.os, 'kill'), \
                    patch.object(process_state, '_read_state', return_value=state):
                with self.assertRaises(AssertionError):
                    process_state.assert_posix_process_stopped(123)
        with patch.object(process_state.os, 'kill', side_effect=PermissionError):
            with self.assertRaises(AssertionError):
                process_state.assert_posix_process_stopped(123)
        for failure in (PermissionError(), OSError(), subprocess.TimeoutExpired('ps', .5)):
            with self.subTest(failure=type(failure).__name__), patch.object(process_state.sys, 'platform', 'darwin'), \
                    patch.object(process_state.subprocess, 'run', side_effect=failure):
                with self.assertRaises(AssertionError):
                    process_state._read_state(123)
        for failure, expected_errno in ((PermissionError(errno.EACCES, 'private read error'), 13),
                                        (OSError(errno.EIO, 'private read error'), 5),
                                        (OSError(), 'unknown'),
                                        (OSError('private errno', 'private read error'), 'unknown')):
            with self.subTest(linux_errno=failure.errno), patch.object(process_state.sys, 'platform', 'linux'), \
                    patch.object(Path, 'read_text', side_effect=failure), \
                    patch.object(process_state.os, 'kill', side_effect=[None, ProcessLookupError]):
                with self.assertRaises(AssertionError) as observed:
                    process_state.assert_posix_process_stopped(123)
                self.assertIn(f'errno={expected_errno}', str(observed.exception))
                self.assertNotIn('private', str(observed.exception))

    def test_state_query_rejects_unknown_output_and_errors(self):
        for code, stdout, stderr in ((0, '', ''), (0, '?', ''), (0, 'Zombie', ''),
                                     (0, 'Z\nS', ''), (2, '', ''), (1, '', 'private-message')):
            result = subprocess.CompletedProcess([], code, stdout, stderr)
            with self.subTest(code=code, stdout=stdout), patch.object(process_state.sys, 'platform', 'darwin'), \
                    patch.object(process_state.subprocess, 'run', return_value=result):
                with self.assertRaises(AssertionError):
                    process_state._read_state(123)
        with patch.object(process_state.sys, 'platform', 'linux'), \
                patch.object(Path, 'read_text', return_value='State:\t?\n'):
            with self.assertRaises(AssertionError):
                process_state._read_state(123)

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-state contract')
    def test_real_live_child_is_rejected_and_killed_unreaped_child_is_accepted(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
        try:
            with self.assertRaises(AssertionError):
                process_state.assert_posix_process_stopped(child.pid)
            os.kill(child.pid, signal.SIGKILL)
            deadline = time.monotonic() + 1
            while process_state._read_state(child.pid) != 'Z':
                self.assertLess(time.monotonic(), deadline, 'child did not enter zombie state')
                time.sleep(.01)
            os.kill(child.pid, 0)  # The old PID-only oracle would still reject it.
            process_state.assert_posix_process_stopped(child.pid)
        finally:
            child.kill()
            child.wait(timeout=2)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux procfs open/read race')
    def test_reaped_between_status_open_and_read_is_confirmed_gone(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
        original_open = Path.open

        def reap_after_open(path, *args, **kwargs):
            status = original_open(path, *args, **kwargs)
            if path == Path(f'/proc/{child.pid}/status'):
                child.kill()
                child.wait(timeout=2)
            return status

        try:
            # The first kill(0) sees a live child. Reaping after open makes the
            # real procfs read raise ESRCH; only the second kill(0) proves gone.
            with patch.object(Path, 'open', reap_after_open):
                process_state.assert_posix_process_stopped(child.pid)
        finally:
            child.kill()
            child.wait(timeout=2)


if __name__ == '__main__':
    unittest.main()

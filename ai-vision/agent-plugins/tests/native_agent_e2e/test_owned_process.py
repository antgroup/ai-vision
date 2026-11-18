"""Owned child execution contracts, using only local Python subprocesses."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from drivers._process import _kill_tree, _run
from _process_test_helpers import assert_posix_process_stopped


class OwnedProcessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'POSIX group permission contract')
    def test_group_permission_error_propagates_without_marking_cleanup_complete(self):
        process = Mock(pid=12345)
        process._owned_group_terminated = False
        with patch('drivers._process.os.killpg', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                _kill_tree(process, None)
        self.assertFalse(process._owned_group_terminated)
        process.kill.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'POSIX group idempotence contract')
    def test_group_cleanup_is_idempotent_after_the_leader_exits(self):
        process = Mock(pid=12345)
        process._owned_group_terminated = False
        process.poll.return_value = 0
        with patch('drivers._process.os.killpg', side_effect=[None, PermissionError]) as kill:
            _kill_tree(process, None)
            _kill_tree(process, None)
        kill.assert_called_once_with(process.pid, signal.SIGKILL)

    def test_runner_keeps_real_nonzero_exit_and_checked_failure_output(self):
        command = [sys.executable, '-c',
            'import sys; print("receipt"); print("diagnostic",file=sys.stderr); sys.exit(17)']
        result = _run(command, text=True, timeout=2)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
            (17, 'receipt\n', 'diagnostic\n'))
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            _run(command, text=True, timeout=2, check=True)
        self.assertEqual(raised.exception.returncode, 17)
        self.assertEqual(raised.exception.stdout, 'receipt\n')

    def test_runner_times_out_an_exited_parent_with_inherited_output_pipes(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'child.pid'
            script = ("import subprocess,sys; from pathlib import Path; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); "
                f"Path({str(receipt)!r}).write_text(str(child.pid)); print('receipt',flush=True)")
            started = time.monotonic()
            try:
                with self.assertRaises(subprocess.TimeoutExpired) as raised:
                    _run([sys.executable, '-c', script], text=True, timeout=1)
                self.assertLess(time.monotonic()-started, 2)
                self.assertEqual(raised.exception.stdout, 'receipt\n')
                self.assertTrue(receipt.exists())
                child = int(receipt.read_text())
                if os.name == 'posix':
                    assert_posix_process_stopped(child, timeout=.5)
                else:
                    listing = subprocess.run(['tasklist', '/FI', f'PID eq {child}', '/NH', '/FO', 'CSV'],
                        capture_output=True, text=True, timeout=2)
                    self.assertNotIn(f'"{child}"', listing.stdout)
            finally:
                if receipt.exists():
                    child = int(receipt.read_text())
                    if os.name == 'posix':
                        try:
                            os.kill(child, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        subprocess.run(['taskkill', '/PID', str(child), '/T', '/F'],
                            capture_output=True, timeout=2)


if __name__ == '__main__':
    unittest.main()

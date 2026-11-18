"""Private process ownership and bounded pipe draining for local E2E children."""
import os
import signal
import subprocess
import sys
import threading
import time


class _WindowsJob:
    """Own the native host and all inherited descendants, including orphaned ones."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t), ("max_working_set", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", Counters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle)))):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def close(self):
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                import ctypes
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


_WINDOWS_BOOTSTRAP = ("import os,subprocess,sys\n"
    "if os.read(0,1) != b'x': raise SystemExit(125)\n"
    "raise SystemExit(subprocess.Popen(sys.argv[1:]).wait())\n")


def _launch(command, **kwargs):
    preserve_stdin = kwargs.get("stdin") == subprocess.PIPE
    if os.name == "nt":
        # Hold a tiny child launcher at a one-byte barrier until its Job Object
        # is assigned. The actual host cannot spawn an uncontained descendant
        # in the race between CreateProcess and AssignProcessToJobObject.
        command = [sys.executable, "-c", _WINDOWS_BOOTSTRAP, *command]
        kwargs["stdin"] = subprocess.PIPE
    process = subprocess.Popen(command, start_new_session=os.name == "posix",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0, **kwargs)
    try:
        job = _WindowsJob(process) if os.name == "nt" else None
        if job is not None:
            process.stdin.write("x" if kwargs.get("text") else b"x")
            process.stdin.flush()
            if not preserve_stdin:
                process.stdin.close()
                process.stdin = None
    except OSError:
        # Refuse to run an uncontained Windows host when job assignment fails.
        try:
            process.kill()
            process.wait(timeout=.5)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
        raise
    return process, job


PIPE_DRAIN_SECONDS = .5
TREE_WAIT_SECONDS = .5


def _kill_tree(process, job):
    """Stop only this launch's owned tree; permission failures remain failures."""
    if os.name == "posix":
        if not getattr(process, "_owned_group_terminated", False):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Do not signal a retiring/reused group again on repeated cleanup.
            process._owned_group_terminated = True
    elif job is not None:
        job.close()
    if process.poll() is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    process.wait(timeout=TREE_WAIT_SECONDS)


def _join_readers(readers, timeout=PIPE_DRAIN_SECONDS):
    end = time.monotonic() + max(0, timeout)
    for reader in readers:
        reader.join(timeout=max(0, end - time.monotonic()))


def _run(command, *, timeout, check=False, capture_output=True, **kwargs):
    """Capture a contained child without trusting descendants to close pipes.

    Reader threads own their streams: closing a buffered stream from another
    thread can block forever behind its read lock. Even an escaped descendant
    cannot extend the command deadline or the bounded final drain.
    """
    if not capture_output:
        raise ValueError('Owned child runner requires captured output')
    deadline = time.monotonic() + timeout
    process, job = _launch(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    outputs = [[], []]
    readers = []
    for stream, output in zip((process.stdout, process.stderr), outputs):
        def read(stream=stream, output=output):
            try:
                for line in stream:
                    output.append(line)
            finally:
                stream.close()
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        readers.append(reader)
    timed_out = False
    try:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
            _join_readers(readers, min(PIPE_DRAIN_SECONDS, max(0, deadline - time.monotonic())))
            timed_out = any(reader.is_alive() for reader in readers)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        try:
            _kill_tree(process, job)
        finally:
            _join_readers(readers)
    empty = '' if kwargs.get('text') or kwargs.get('encoding') or kwargs.get('universal_newlines') else b''
    stdout, stderr = (empty.join(output) for output in outputs)
    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result

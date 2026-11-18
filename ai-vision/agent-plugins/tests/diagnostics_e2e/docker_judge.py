"""Run the fake Judge on the owned platform network on Linux and macOS."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

_factory = ContextVar("owned_judge_factory", default=None)


def create_judge(local_factory):
    factory = _factory.get()
    return factory() if factory is not None else local_factory()


@contextmanager
def platform_judges(container):
    images = []
    preparation_error = []

    def factory():
        if preparation_error:
            raise RuntimeError(
                "Owned Judge dependency preparation already failed"
            ) from preparation_error[0]
        if not images:
            base = json.loads(_docker("inspect", container))[0]["Image"]
            try:
                images.append(_build_judge_image(base))
            except Exception as exc:
                preparation_error.append(exc)
                raise
        return DockerJudge(container, image=images[0])

    token = _factory.set(factory)
    try:
        yield
    finally:
        primary = sys.exception()
        _factory.reset(token)
        errors = []
        for image in images:
            try:
                _docker("image", "rm", image)
            except BaseException as cleanup:
                errors.append(cleanup)
        if errors:
            description = "; ".join(
                f"{type(error).__name__}: {error}" for error in errors
            )
            cleanup = (
                errors[0]
                if len(errors) == 1
                else BaseExceptionGroup(description, errors)
            )
            if primary is None:
                raise cleanup
            if not isinstance(primary, Exception):
                primary.add_note("Owned Judge cleanup failed: " + description)
                raise primary from cleanup
            raise BaseExceptionGroup(
                f"Owned Judge invocation and cleanup failed: {type(primary).__name__}: {primary}; {description}",
                [primary, *errors],
            ) from None


def _docker(*args, env=None):
    return subprocess.run(
        ["docker", *args],
        check=True,
        text=True,
        capture_output=True,
        timeout=600 if args[0] == "build" else 120,
        env=env,
    ).stdout.strip()


def _build_judge_image(base):
    image = "aiv-e2e-judge:" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="aiv-e2e-judge-build-") as directory:
        work = Path(directory)
        architecture = _docker(
            "image", "inspect", "--format", "{{.Architecture}}", base
        )
        target = {"amd64": "x86_64", "arm64": "aarch64"}.get(architecture)
        if target is None:
            raise ValueError("Unsupported Judge image architecture")
        version = _docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/app/.venv/bin/python",
            base,
            "-c",
            "import sys; print(str(sys.version_info.major)+str(sys.version_info.minor))",
        )
        if version not in {"311", "312"}:
            raise ValueError("Unsupported Judge image Python version")
        # Download on the host, where the harness dependencies were installed.
        # The container build then has no external package-network dependency.
        wheels = work / "wheels"
        wheels.mkdir()
        cached = os.environ.get("AIVISION_E2E_JUDGE_WHEELHOUSE")
        if cached:
            source = Path(cached).expanduser().resolve()
            files = sorted(source.glob("*.whl")) if source.is_dir() else []
            if not files or any(path.is_symlink() for path in files):
                raise ValueError("Judge wheelhouse requires regular wheel files")
            for path in files:
                shutil.copyfile(path, wheels / path.name)
        else:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "download",
                    "--index-url",
                    "https://pypi.org/simple",
                    "--dest",
                    str(wheels),
                    "--only-binary=:all:",
                    "--implementation",
                    "cp",
                    "--abi",
                    "cp" + version,
                    "--python-version",
                    version,
                    "--platform",
                    "manylinux2014_" + target,
                    "--platform",
                    "manylinux_2_28_" + target,
                    "--platform",
                    "manylinux_2_34_" + target,
                    "langchain-core==1.6.6",
                ],
                check=True,
                text=True,
                capture_output=True,
                timeout=600,
                env={
                    **{
                        key: value
                        for key, value in os.environ.items()
                        if not key.startswith("PIP_")
                    },
                    "PIP_CONFIG_FILE": os.devnull,
                },
            )
        dockerfile = work / "Dockerfile"
        dockerfile.write_text(
            "FROM " + base + "\nUSER root\nCOPY wheels /tmp/judge-wheels\n"
            "RUN /app/.venv/bin/python -m ensurepip && "
            "/app/.venv/bin/python -m pip install --no-index --find-links=/tmp/judge-wheels "
            "langchain-core==1.6.6 && rm -rf /tmp/judge-wheels\n"
        )
        try:
            _docker(
                "build",
                "--pull=false",
                "--force-rm",
                "--network",
                "none",
                "--tag",
                image,
                directory,
                env={**os.environ, "DOCKER_BUILDKIT": "0"},
            )
        except BaseException:
            subprocess.run(
                ["docker", "image", "rm", image], capture_output=True, timeout=30
            )
            raise
    return image


def _write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


class DockerJudge:
    def __init__(self, platform_container, *, image=None):
        self.image = image
        self.owns_image = image is None
        self.platform_container = platform_container
        self.name = "aiv-e2e-judge-" + uuid.uuid4().hex
        self.mode_value = "normal"
        self.state = {"requests": [], "errors": []}

    def _read(self):
        if getattr(self, "active", False):
            raw = _docker(
                "exec",
                self.name,
                "/app/.venv/bin/python",
                "-c",
                "from pathlib import Path; p=Path('/tmp/aiv-judge-state/state.json'); "
                "print(p.read_text() if p.exists() else '{}')",
            )
            state = json.loads(raw)
            if state:
                self.state = state
        elif hasattr(self, "directory"):
            path = self.directory / "state.json"
            if path.exists():
                self.state = json.loads(path.read_text())
        return self.state

    @property
    def requests(self):
        return (
            self._read()["requests"]
            if hasattr(self, "directory")
            else self.state["requests"]
        )

    @property
    def errors(self):
        return (
            self._read()["errors"]
            if hasattr(self, "directory")
            else self.state["errors"]
        )

    @property
    def mode(self):
        return self.mode_value

    @mode.setter
    def mode(self, value):
        if value not in {"normal", "malformed", "http_failure"}:
            raise ValueError("unsupported fake judge mode")
        _docker(
            "exec",
            self.name,
            "/app/.venv/bin/python",
            "-c",
            "import json,sys; from pathlib import Path; "
            "p=Path('/tmp/aiv-judge-state/mode.tmp'); "
            "p.write_text(json.dumps(sys.argv[1])); p.replace(p.with_suffix('.json'))",
            value,
        )
        self._wait(lambda: self._read().get("mode") == value)
        self.mode_value = value

    def _wait(self, ready):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready():
                return
            time.sleep(0.05)
        raise TimeoutError("Owned Docker Judge did not become ready")

    def __enter__(self):
        details = json.loads(_docker("inspect", self.platform_container))[0]
        networks = list(details["NetworkSettings"]["Networks"])
        if len(networks) != 1:
            raise ValueError("Judge requires the single owned platform network")
        if self.image is None:
            self.image = _build_judge_image(details["Image"])
        self.temporary = tempfile.TemporaryDirectory(prefix="aiv-e2e-judge-")
        self.directory = Path(self.temporary.name)
        self.active = False
        try:
            _docker(
                "run",
                "--detach",
                "--name",
                self.name,
                "--network",
                networks[0],
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--mount",
                f"type=bind,source={Path(__file__).resolve().parent},target=/e2e,readonly",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--entrypoint",
                "/app/.venv/bin/python",
                self.image,
                "/e2e/docker_judge.py",
                "--serve",
            )
            self.active = True
            self._wait(lambda: self._read().get("ready") is True)
            self.url = f"http://{self.name}:8080/v1"
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, error_type, *_args):
        errors = []

        def cleanup(action):
            try:
                action()
            except BaseException as exc:
                errors.append(exc)

        # SIGTERM flushes the final request evidence before removal.
        if self.active:
            cleanup(lambda: _docker("stop", "--time", "10", self.name))
            self.active = False
            cleanup(
                lambda: _docker(
                    "cp",
                    self.name + ":/tmp/aiv-judge-state/state.json",
                    str(self.directory / "state.json"),
                )
            )
            cleanup(self._read)
        cleanup(lambda: _docker("rm", "--force", self.name))
        cleanup(self.temporary.cleanup)
        if self.owns_image:
            cleanup(lambda: _docker("image", "rm", self.image))
        if errors:
            self.state["errors"].extend(type(exc).__name__ for exc in errors)
            if error_type is None:
                raise errors[0]


def serve():
    from providers import FakeJudge

    stopped = False

    def stop(*_args):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    state = Path("/tmp/aiv-judge-state")
    state.mkdir(mode=0o700)
    _write(state / "mode.json", "normal")
    with FakeJudge(bind_host="0.0.0.0", bind_port=8080) as judge:
        while not stopped:
            mode = json.loads((state / "mode.json").read_text())
            with judge.lock:
                judge.mode = mode
                _write(
                    state / "state.json",
                    {
                        "ready": True,
                        "mode": mode,
                        "requests": judge.requests,
                        "errors": judge.errors,
                    },
                )
            time.sleep(0.05)
    _write(
        state / "state.json",
        {
            "ready": False,
            "mode": judge.mode,
            "requests": judge.requests,
            "errors": judge.errors,
        },
    )


if __name__ == "__main__":
    serve()

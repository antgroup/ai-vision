from pathlib import Path

from agentlens.diagnoser.executors.rule.custom import sandbox


def test_source_worker_retains_isolated_interpreter(monkeypatch, tmp_path):
    script = tmp_path / "worker_main.py"
    script.touch()
    monkeypatch.setattr(sandbox, "_WORKER_SCRIPT", str(script))
    monkeypatch.setattr(sandbox, "_NATIVE_WORKER", tmp_path / "agentlens-rule-worker")
    command = sandbox.worker_command()
    assert command[1:] == ["-I", "-S", str(script)]


def test_native_worker_does_not_require_python_source(monkeypatch, tmp_path):
    script = tmp_path / "worker_main.py"
    native = tmp_path / "agentlens-rule-worker"
    native.touch(mode=0o755)
    monkeypatch.setattr(sandbox, "_WORKER_SCRIPT", str(script))
    monkeypatch.setattr(sandbox, "_NATIVE_WORKER", native)
    assert sandbox.worker_command() == [str(native)]
    assert not Path(script).exists()

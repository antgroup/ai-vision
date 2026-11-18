from io import StringIO
from time import time

import agentlens.logging as logging_module
from agentlens.logging import get_logger, init_logging, shutdown_logging


def test_logger_emits_required_fields():
    buf = StringIO()
    init_logging(stream=buf, instance_uuid="h/i", level="INFO")
    log = get_logger("test")
    log.info("dispatch.cas_fail", task_uuid="t-abcd1234")
    shutdown_logging()

    line = buf.getvalue().strip().splitlines()[-1]
    assert "dispatch.cas_fail" in line
    assert "h/i" in line
    assert "t-abcd1234" in line
    assert "info" in line


def test_file_routing(tmp_path):
    init_logging(log_dir=tmp_path, instance_uuid="h/i", level="INFO")

    get_logger("dispatch").info("dispatch.tick")
    get_logger("task-cleanup").info("task-cleanup.start")
    get_logger("elector").info("elector.tick")
    get_logger("runner").info("runner.tick")
    get_logger("custom-rule-worker-pool").info("pool.started", size=2)
    get_logger("signoz").info("signoz.query")
    get_logger("ob").info("ob.query", sql="SELECT 1")

    shutdown_logging()

    assert "dispatch.tick" in (tmp_path / "scheduler.log").read_text()
    assert "task-cleanup.start" in (tmp_path / "scheduler.log").read_text()
    assert "elector.tick" in (tmp_path / "election.log").read_text()
    assert "runner.tick" in (tmp_path / "executor.log").read_text()
    assert "pool.started" in (tmp_path / "executor.log").read_text()
    assert "signoz.query" in (tmp_path / "executor.log").read_text()
    assert "ob.query" in (tmp_path / "ob.log").read_text()


def test_unmapped_logger_falls_back_to_executor(tmp_path):
    init_logging(log_dir=tmp_path, instance_uuid="h/i", level="INFO")

    get_logger("unknown-module").info("fallback.event")

    shutdown_logging()

    assert "fallback.event" in (tmp_path / "executor.log").read_text()


def test_stdout_tee(tmp_path, capsys):
    init_logging(log_dir=tmp_path, instance_uuid="h/i", level="INFO", log_stdout=True)

    get_logger("dispatch").info("tee_test")

    shutdown_logging()

    captured = capsys.readouterr()
    assert "tee_test" in captured.out
    assert "tee_test" in (tmp_path / "scheduler.log").read_text()


def test_file_logs_roll_daily_and_keep_configured_backups(tmp_path):
    init_logging(
        log_dir=tmp_path,
        instance_uuid="h/i",
        level="INFO",
        log_retention_days=2,
    )
    log = get_logger("runner")
    log.info("before_rollover")

    factory = logging_module._factory
    assert factory is not None
    target = factory._file_targets["executor.log"]
    assert target.handler.when == "MIDNIGHT"
    assert target.handler.backupCount == 2

    for suffix in ("2020-01-01", "2021-01-01", "2022-01-01"):
        (tmp_path / f"executor.log.{suffix}").write_text(suffix)

    target.handler.rolloverAt = int(time()) - 1
    log.info("after_rollover")
    shutdown_logging()

    assert "after_rollover" in (tmp_path / "executor.log").read_text()
    rotated = list(tmp_path.glob("executor.log.*"))
    assert len(rotated) == 2
    assert not (tmp_path / "executor.log.2020-01-01").exists()
    assert not (tmp_path / "executor.log.2021-01-01").exists()
    assert any("before_rollover" in path.read_text() for path in rotated)


def test_debug_events_are_hidden_at_info_level():
    buf = StringIO()
    init_logging(stream=buf, instance_uuid="h/i", level="INFO")

    get_logger("runner").debug("runner.record_written", task_uuid="t-1")
    get_logger("runner").info("runner.success", task_uuid="t-1")
    shutdown_logging()

    text = buf.getvalue()
    assert "runner.record_written" not in text
    assert "runner.success" in text

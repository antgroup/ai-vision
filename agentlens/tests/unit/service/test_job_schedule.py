import pytest

from agentlens.domain.enums import JobTriggerType
from agentlens.domain.errors import Validation
from agentlens.service.job_schedule import _schedule_cron_error, validate_job_schedule


@pytest.mark.parametrize(
    "schedule_cron",
    [
        "* * * * *",
        "0 * * * *",
        "0 0 * * *",
        "0 0 * * 1",
    ],
)
def test_validate_job_schedule_accepts_intervals_up_to_seven_days(schedule_cron):
    validate_job_schedule(JobTriggerType.SCHEDULE, schedule_cron)


@pytest.mark.parametrize(
    ("schedule_cron", "message"),
    [
        (None, "required"),
        ("", "required"),
        ("not a cron", "five-field"),
        ("0 0 1 * *", "7 days"),
    ],
)
def test_validate_job_schedule_rejects_missing_invalid_or_long_cron(schedule_cron, message):
    with pytest.raises(Validation, match=message):
        validate_job_schedule(JobTriggerType.SCHEDULE, schedule_cron)


def test_validate_job_schedule_ignores_cron_for_manual_job():
    validate_job_schedule(JobTriggerType.MANUAL, None)
    validate_job_schedule(JobTriggerType.MANUAL, "not a cron")


def test_validate_job_schedule_caches_rejected_cron():
    _schedule_cron_error.cache_clear()

    for _ in range(2):
        with pytest.raises(Validation, match="7 days"):
            validate_job_schedule(JobTriggerType.SCHEDULE, "0 0 1 * *")

    assert _schedule_cron_error.cache_info().hits == 1

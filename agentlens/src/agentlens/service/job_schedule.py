from datetime import datetime, timedelta
from functools import lru_cache

from croniter import croniter

from agentlens.domain.enums import JobTriggerType
from agentlens.domain.errors import Validation

_VALIDATION_START = datetime(2024, 1, 1)
_VALIDATION_END = datetime(2032, 1, 1)
_MAX_INTERVAL = timedelta(days=7)


@lru_cache(maxsize=256)
def _schedule_cron_error(schedule_cron: str) -> str | None:
    fields = schedule_cron.split()
    if len(fields) != 5 or not croniter.is_valid(schedule_cron):
        return "schedule_cron must be a valid five-field cron expression"

    anchor = _VALIDATION_START
    while anchor < _VALIDATION_END:
        iterator = croniter(schedule_cron, anchor)
        first = iterator.get_next(datetime)
        second = iterator.get_next(datetime)
        if first - anchor > _MAX_INTERVAL or second - first > _MAX_INTERVAL:
            return "schedule_cron interval must not exceed 7 days"
        anchor += timedelta(days=1)
    return None


def validate_job_schedule(
    trigger_type: JobTriggerType | str,
    schedule_cron: str | None,
) -> None:
    if trigger_type != JobTriggerType.SCHEDULE:
        return
    if not schedule_cron or not schedule_cron.strip():
        raise Validation("schedule_cron is required when trigger_type is schedule")
    error = _schedule_cron_error(" ".join(schedule_cron.split()))
    if error is not None:
        raise Validation(error)

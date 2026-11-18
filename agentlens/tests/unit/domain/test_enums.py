from agentlens.domain.enums import (
    DiagnoserMode,
    DiagnoserType,
    JobStatus,
    JobTriggerType,
    ReviewStatus,
    TargetType,
    TaskStatus,
    Verdict,
)


def test_diagnoser_enums():
    assert DiagnoserType.BUILTIN.value == "builtin"
    assert DiagnoserType.CUSTOM.value == "custom"
    assert DiagnoserMode.RULE.value == "rule"
    assert DiagnoserMode.LLM.value == "llm"


def test_job_enums():
    assert {e.value for e in JobTriggerType} == {"schedule", "manual"}
    assert {e.value for e in JobStatus} == {"enabled", "disabled"}


def test_task_status_full_set():
    assert {e.value for e in TaskStatus} == {
        "pending",
        "dispatched",
        "running",
        "succeeded",
        "failed",
        "canceled",
    }


def test_diagnosis_record_enums():
    assert {e.value for e in Verdict} == {"good", "bad", "failed"}
    assert {e.value for e in ReviewStatus} == {
        "pending",
        "skipped",
        "confirmed",
        "falsePositive",
        "cannotJudge",
    }
    assert TargetType.TRACE.value == "trace"

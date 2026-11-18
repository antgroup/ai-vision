from enum import Enum


class DiagnoserType(str, Enum):
    BUILTIN = "builtin"
    CUSTOM = "custom"


class DiagnoserMode(str, Enum):
    RULE = "rule"
    LLM = "llm"


class DatasetType(str, Enum):
    SIGNOZ = "signoz"


class JobTriggerType(str, Enum):
    SCHEDULE = "schedule"
    MANUAL = "manual"


class JobStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class ModelConnectionStatus(str, Enum):
    UNTESTED = "untested"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ModelConnectionTestError(str, Enum):
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    TIMEOUT = "timeout"
    CONNECTION_FAILED = "connection_failed"
    INVALID_REQUEST = "invalid_request"
    UNAVAILABLE = "unavailable"


class TaskStatus(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class Verdict(str, Enum):
    GOOD = "good"
    BAD = "bad"
    FAILED = "failed"


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    SKIPPED = "skipped"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "falsePositive"
    CANNOT_JUDGE = "cannotJudge"


class TargetType(str, Enum):
    TRACE = "trace"

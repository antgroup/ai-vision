from pydantic import BaseModel

from agentlens.domain.enums import ReviewStatus


class FeedbackContent(BaseModel):
    remark: str | None = None


class FeedbackSubmit(BaseModel):
    review_status: ReviewStatus
    feedback: FeedbackContent | None = None
    reviewed_by: str | None = None

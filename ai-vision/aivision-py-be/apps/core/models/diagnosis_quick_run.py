"""Durable provisioning receipt; execution remains an AgentLens Task."""

from uuid import uuid4

from django.db import models


def owned_uuid():
    return uuid4().hex


class DiagnosisQuickRunRequest(models.Model):
    uuid = models.CharField(max_length=32, unique=True, default=owned_uuid)
    space = models.ForeignKey("Space", on_delete=models.PROTECT, db_constraint=False)
    user = models.ForeignKey("User", on_delete=models.PROTECT, db_constraint=False)
    agent = models.ForeignKey("Agent", on_delete=models.PROTECT, db_constraint=False)
    request_id = models.CharField(max_length=36)
    canonical_input = models.JSONField()
    input_digest = models.CharField(max_length=64)
    resolved_plan = models.JSONField()
    plan_digest = models.CharField(max_length=64)
    dataset_uuid = models.CharField(max_length=32, unique=True, default=owned_uuid)
    job_uuid = models.CharField(max_length=32, unique=True, default=owned_uuid)
    task_uuid = models.CharField(max_length=32, unique=True, default=owned_uuid)
    provisioning_step = models.CharField(max_length=24, default="prepared")
    lease_epoch = models.PositiveBigIntegerField(default=0)
    lease_until = models.DateTimeField(null=True)
    safe_error_code = models.CharField(max_length=64, default="")
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True)

    class Meta:
        db_table = "aivision_diagnosis_quick_run_request"
        constraints = [
            models.UniqueConstraint(
                fields=["space", "user", "request_id"],
                name="uk_quick_space_user_request",
            )
        ]

"""Management-only installation receipts; execution stays on the operator host."""

from django.db import models

from .user import generate_uuid


class SandboxInstallationSession(models.Model):
    uuid = models.CharField(max_length=36, unique=True, default=generate_uuid)
    space = models.ForeignKey("Space", on_delete=models.CASCADE, db_constraint=False)
    creator = models.ForeignKey("User", on_delete=models.CASCADE, db_constraint=False)
    operation = models.CharField(max_length=16)
    mode = models.CharField(max_length=16, default="manual")
    bootstrap_plan = models.JSONField(null=True, blank=True)
    bootstrap_token_hash = models.CharField(
        max_length=64, unique=True, null=True, blank=True
    )
    enrollment_token_hash = models.CharField(
        max_length=64, unique=True, null=True, blank=True
    )
    enrollment_key_hash = models.CharField(max_length=64, default="")
    enrollment_platform = models.CharField(max_length=32, default="")
    registration_state = models.CharField(max_length=16, default="pending")
    connection_uuid = models.CharField(max_length=32, null=True, blank=True)
    configuration = models.JSONField()
    report_token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    run_id = models.CharField(max_length=32, default="")
    status = models.CharField(max_length=16, default="waiting")
    stages = models.JSONField(default=list)
    events = models.JSONField(default=list)
    last_seq = models.PositiveBigIntegerField(default=0)
    event_count = models.PositiveBigIntegerField(default=0)
    last_received_at = models.DateTimeField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "aivision_sandbox_installation_session"
        indexes = [
            models.Index(
                fields=["space", "creator", "gmt_create"],
                name="idx_sandbox_install_scope_time",
            ),
        ]

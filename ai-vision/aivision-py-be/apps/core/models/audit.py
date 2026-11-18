from django.db import models

from .space import Space
from .user import User


class AuthAuditLog(models.Model):
    event = models.CharField(max_length=64)
    success = models.BooleanField()
    actor_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="auth_audit_logs",
        db_constraint=False,
        null=True,
        blank=True,
    )
    space = models.ForeignKey(
        Space,
        on_delete=models.SET_NULL,
        related_name="auth_audit_logs",
        db_constraint=False,
        null=True,
        blank=True,
    )
    session_uuid = models.CharField(max_length=36, null=True, blank=True)
    request_id = models.CharField(max_length=128, null=True, blank=True)
    source_ip = models.CharField(max_length=64, null=True, blank=True)
    user_agent = models.CharField(max_length=512, null=True, blank=True)
    error_key = models.CharField(max_length=64, null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "aivision_auth_audit_log"
        indexes = [
            models.Index(
                fields=["event", "gmt_create"], name="idx_auth_audit_event_time"
            ),
            models.Index(
                fields=["actor_user", "gmt_create"], name="idx_auth_audit_user_time"
            ),
        ]

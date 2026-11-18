from django.db import models

from .user import User, generate_uuid


class AuthSession(models.Model):
    STATUS_REVOKED = 0
    STATUS_ACTIVE = 1
    STATUS_CHOICES = [(STATUS_REVOKED, "Revoked"), (STATUS_ACTIVE, "Active")]

    session_uuid = models.CharField(
        max_length=36, unique=True, editable=False, default=generate_uuid
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="auth_sessions",
        db_constraint=False,
    )
    status = models.SmallIntegerField(default=STATUS_ACTIVE, choices=STATUS_CHOICES)
    expires_at = models.DateTimeField(db_index=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.CharField(max_length=64, null=True, blank=True)
    created_ip = models.CharField(max_length=64, null=True, blank=True)
    user_agent = models.CharField(max_length=512, null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=128, null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "aivision_auth_session"
        indexes = [
            models.Index(
                fields=["user", "status", "expires_at"],
                name="idx_auth_session_user_state",
            ),
            models.Index(
                fields=["status", "expires_at"],
                name="idx_auth_session_state_exp",
            ),
        ]


class AuthRefreshToken(models.Model):
    session = models.ForeignKey(
        AuthSession,
        on_delete=models.CASCADE,
        related_name="refresh_tokens",
        db_constraint=False,
    )
    token_hash = models.CharField(max_length=64, unique=True)
    generation = models.PositiveIntegerField()
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "aivision_auth_refresh_token"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "generation"],
                name="uk_auth_refresh_generation",
            )
        ]

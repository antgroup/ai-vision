from django.db import models

from .space import Space
from .user import User, generate_uuid


class SpaceInvitation(models.Model):
    STATUS_PENDING = "Pending"
    STATUS_ACCEPTED = "Accepted"
    STATUS_REVOKED = "Revoked"
    STATUS_EXPIRED = "Expired"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_EXPIRED, "Expired"),
    ]

    invitation_uuid = models.CharField(
        max_length=36, unique=True, editable=False, default=generate_uuid
    )
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name="invitations",
        db_constraint=False,
    )
    normalized_email = models.CharField(max_length=256)
    role = models.SmallIntegerField(
        choices=[(1, "Admin"), (2, "Developer"), (3, "Member")]
    )
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=16, default=STATUS_PENDING, choices=STATUS_CHOICES
    )
    pending_marker = models.SmallIntegerField(default=1, null=True, blank=True)
    invited_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="sent_space_invitations",
        db_constraint=False,
    )
    expires_at = models.DateTimeField()
    accepted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="accepted_space_invitations",
        db_constraint=False,
        null=True,
        blank=True,
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="revoked_space_invitations",
        db_constraint=False,
        null=True,
        blank=True,
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "aivision_space_invitation"
        constraints = [
            models.UniqueConstraint(
                fields=["space", "normalized_email", "pending_marker"],
                name="uk_space_pending_invitation",
            )
        ]
        indexes = [
            models.Index(
                fields=["space", "status", "expires_at"],
                name="idx_invitation_space_state",
            )
        ]

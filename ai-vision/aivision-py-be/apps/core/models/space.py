import hashlib
import secrets

from django.core.exceptions import ValidationError
from django.db import models

from .user import User, generate_uuid


class Space(models.Model):
    TYPE_CHOICES = [
        (1, "Personal"),
        (2, "Business"),
    ]
    STATUS_CHOICES = [
        (0, "Disabled"),
        (1, "Normal"),
    ]
    SCOPE_CHOICES = [
        (1, "Private"),
        (2, "Public"),
    ]

    uuid = models.CharField(
        max_length=36, unique=True, editable=False, default=generate_uuid
    )
    name = models.CharField(max_length=128)
    type = models.SmallIntegerField(choices=TYPE_CHOICES)
    scope = models.SmallIntegerField(default=1, choices=SCOPE_CHOICES)
    description = models.CharField(max_length=512, null=True, blank=True)
    billing_owner = models.CharField(max_length=256, null=True, blank=True)
    creator = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="created_spaces",
        db_constraint=False,
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="owned_spaces",
        db_constraint=False,
        null=True,
        blank=True,
    )
    status = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_space"
        ordering = ["-gmt_create"]

    def __str__(self):
        return self.name


class SpaceMember(models.Model):
    ROLE_CHOICES = [
        (1, "Admin"),
        (2, "Developer"),
        (3, "Member"),
    ]

    space = models.ForeignKey(
        Space, on_delete=models.CASCADE, related_name="members", db_constraint=False
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="space_memberships",
        db_constraint=False,
    )
    role = models.SmallIntegerField(default=3, choices=ROLE_CHOICES)
    joined_at = models.DateTimeField(auto_now_add=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_space_member"
        unique_together = ["space", "user"]
        ordering = ["-joined_at"]
        indexes = [
            models.Index(
                fields=["user", "gmt_deleted"], name="idx_member_user_deleted"
            ),
            models.Index(
                fields=["space", "gmt_deleted", "role"],
                name="idx_member_space_state_role",
            ),
        ]

    def __str__(self):
        return f"{self.user.name} in {self.space.name}"


class ApiKey(models.Model):
    STATUS_CHOICES = [
        (0, "Disabled"),
        (1, "Active"),
        (2, "Expired"),
    ]
    LANGFUSE_MAPPING_CHOICES = [
        (0, "Not Mapped"),
        (1, "Mapped"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="api_keys", db_constraint=False
    )
    space = models.ForeignKey(
        Space, on_delete=models.CASCADE, related_name="api_keys", db_constraint=False
    )
    key_hash = models.CharField(max_length=128, unique=True)
    key_prefix = models.CharField(max_length=32)
    name = models.CharField(max_length=128, null=True, blank=True)
    source_platform = models.CharField(max_length=64, null=True, blank=True)
    langfuse_project_id = models.CharField(max_length=128, null=True, blank=True)
    langfuse_project_name = models.CharField(max_length=256, null=True, blank=True)
    langfuse_mapping_status = models.SmallIntegerField(
        default=0, choices=LANGFUSE_MAPPING_CHOICES
    )
    status = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_api_key"
        ordering = ["-gmt_create"]
        verbose_name = "[废弃] Api Key"
        verbose_name_plural = "[废弃] Api Keys"

    def __str__(self):
        return f"{self.key_prefix}... ({self.name or 'Unnamed'})"

    @classmethod
    def generate_key(cls):
        raw_key = secrets.token_hex(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_prefix = f"pk-aiv-{raw_key[:8]}"
        return raw_key, key_hash, key_prefix

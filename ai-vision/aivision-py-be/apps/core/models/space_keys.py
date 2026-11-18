import hashlib
import uuid

from django.core.exceptions import ValidationError
from django.db import models

from .space import Space
from .user import User


class SpaceKeyManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(gmt_deleted__isnull=True)


class SpaceKey(models.Model):
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name="space_keys",
        db_constraint=False,
    )
    key_prefix = models.CharField(max_length=20)
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    onboarding_agent = models.ForeignKey(
        "Agent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        db_constraint=False,
    )
    onboarding_ciphertext = models.TextField(null=True, blank=True, editable=False)
    name = models.CharField(max_length=128, blank=True, default="")
    key_alias = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_comment="空间内SpaceKey稳定唯一标识",
    )
    creator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_space_keys",
        db_constraint=False,
    )
    enabled = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    objects = SpaceKeyManager()
    all_objects = models.Manager()

    class Meta:
        db_table = "aivision_space_key"
        ordering = ["-gmt_create"]
        constraints = [
            models.UniqueConstraint(
                fields=["space", "key_alias"],
                name="uk_space_key_alias",
            ),
        ]

    def __str__(self):
        return f"{self.key_prefix}... ({self.name or 'Unnamed'})"

    def clean(self):
        # 创建策略收敛到 services/space_key_policy，避免散落的空间类型判断。
        from ..services.space_key_policy import can_create_for

        if self.space_id and not can_create_for(self.space):
            raise ValidationError("Space Keys are not supported for this space type")

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def generate_key(cls):
        # 完整 key = "sk-aiv-" + uuid4 hex（32 位，无横线）。
        # sk-aiv- 前缀是 key 的真实组成部分（鉴权与 hash 均基于完整 key）。
        uuid_hex = uuid.uuid4().hex
        raw_key = f"sk-aiv-{uuid_hex}"
        key_prefix = f"sk-aiv-{uuid_hex[:8]}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        return raw_key, key_prefix, key_hash

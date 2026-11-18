import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models


def generate_uuid():
    return str(uuid.uuid4())


class User(models.Model):
    STATUS_DISABLED = 0
    STATUS_NORMAL = 1
    STATUS_PENDING = 2
    STATUS_CHOICES = [
        (STATUS_DISABLED, "Disabled"),
        (STATUS_NORMAL, "Normal"),
        (STATUS_PENDING, "Pending"),
    ]

    uuid = models.CharField(
        max_length=36, unique=True, editable=False, default=generate_uuid
    )
    password = models.CharField(max_length=256)
    name = models.CharField(max_length=128)
    email = models.CharField(max_length=256)
    normalized_email = models.CharField(max_length=256, unique=True)
    avatar_url = models.CharField(max_length=512, null=True, blank=True)
    profile_extra = models.TextField(null=True, blank=True)
    status = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    product_guide_pending = models.BooleanField(default=False)
    last_login_at = models.DateTimeField(null=True, blank=True)
    last_login_ip = models.CharField(max_length=64, null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_user"
        ordering = ["-gmt_create"]

    def __str__(self):
        return f"{self.name} ({self.normalized_email or self.uuid})"

    @staticmethod
    def normalize_email(value):
        if value is None:
            return None
        normalized = str(value).strip().lower()
        return normalized or None

    def set_password(self, raw_password):
        if raw_password is None:
            raise ValidationError({"password": "Password is required"})
        self.password = make_password(raw_password)

    def set_unusable_password(self):
        self.password = make_password(None)

    def check_password(self, raw_password):
        return bool(self.password) and check_password(raw_password, self.password)

    def has_usable_password(self):
        return bool(self.password) and not self.password.startswith("!")

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

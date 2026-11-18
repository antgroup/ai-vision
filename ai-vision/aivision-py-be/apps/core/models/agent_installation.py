"""One-use browser authorization and the resulting Agent-only ingest credential."""

import hashlib
import secrets

from django.db import models

from .user import generate_uuid


class AgentInstallation(models.Model):
    uuid = models.CharField(max_length=36, unique=True, default=generate_uuid)
    agent = models.ForeignKey(
        "Agent",
        on_delete=models.CASCADE,
        related_name="installations",
        db_constraint=False,
    )
    creator = models.ForeignKey(
        "User",
        on_delete=models.CASCADE,
        related_name="agent_installations",
        db_constraint=False,
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    exchanged_at = models.DateTimeField(null=True, blank=True)
    credential_hash = models.CharField(
        max_length=64, unique=True, null=True, blank=True
    )
    enabled = models.BooleanField(default=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "aivision_agent_installation"

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    @classmethod
    def generate_secret(cls, prefix):
        raw = prefix + secrets.token_urlsafe(32)
        return raw, cls.digest(raw)

    @property
    def space(self):
        return self.agent.space

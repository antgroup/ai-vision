from django.db import models

from .agent import Agent


class PlatformConfig(models.Model):
    platform_code = models.CharField(max_length=64, unique=True)
    platform_name = models.CharField(max_length=128)
    default_agent_type = models.CharField(max_length=64, null=True, blank=True)
    api_endpoint = models.CharField(max_length=512, null=True, blank=True)
    auto_create_space = models.SmallIntegerField(default=0)
    auto_bind_user = models.SmallIntegerField(default=1)
    config = models.JSONField(null=True, blank=True)
    status = models.SmallIntegerField(default=1)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "aivision_platform_config"
        db_table_comment = "AI Vision 平台配置表"
        ordering = ["platform_name"]
        verbose_name = "Platform config (aivision配置)"
        verbose_name_plural = "Platform configs (aivision配置)"

    def __str__(self):
        return self.platform_name


class Session(models.Model):
    STATUS_CHOICES = [
        (1, "Success"),
        (2, "Failed"),
    ]

    agent = models.ForeignKey(
        Agent, on_delete=models.CASCADE, related_name="sessions", db_constraint=False
    )
    trace_id = models.CharField(max_length=128)
    status = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    tokens_input = models.BigIntegerField(default=0)
    tokens_output = models.BigIntegerField(default=0)
    error_type = models.CharField(max_length=128, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "aivision_session"
        ordering = ["-gmt_create"]
        verbose_name = "[废弃] Session"
        verbose_name_plural = "[废弃] Sessions"

    def __str__(self):
        return f"Session {self.trace_id}"

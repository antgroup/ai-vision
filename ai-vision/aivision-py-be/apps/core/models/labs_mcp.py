"""Labs MCP observability models."""

from django.db import models

from .space import Space
from .user import generate_uuid


class McpServerConfig(models.Model):
    """An MCP server configuration imported from paste JSON.

    Server stats come from tool spans the agent plugins already emit. This
    model stores only the sanitized user-provided config the frontend parsed
    so that the page can show launch commands and env / header key names.
    Secret values are masked by both the frontend parser and backend before saving.
    """

    id = models.CharField(
        max_length=36, primary_key=True, default=generate_uuid, editable=False
    )
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name="mcp_server_configs",
        db_constraint=False,
    )
    server_id = models.CharField(max_length=128)
    description = models.CharField(max_length=512, blank=True, default="")
    transport = models.CharField(max_length=16, blank=True, default="")
    command = models.CharField(max_length=512, blank=True, default="")
    args = models.JSONField(null=True, blank=True)
    url = models.CharField(max_length=1024, blank=True, default="")
    env_keys = models.JSONField(default=list, blank=True)
    header_keys = models.JSONField(default=list, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    # Optional memory-tool semantics declared by the importer: which tools of
    # this server search / write / list / manage memories. Memory observability
    # keys off this declaration instead of a hardcoded server-name list, so a
    # new memory backend becomes observable with zero code change. Absent
    # (None) servers may still be recognized from their span result shapes.
    memory_semantics = models.JSONField(null=True, blank=True)
    # Full tools/list snapshot fetched through the MCP protocol. Header /
    # env values used for the fetch are request-scoped and never stored here.
    tools = models.JSONField(default=list, blank=True)
    tools_synced_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_labs_mcp_server_config"
        unique_together = [("space", "server_id")]

    def __str__(self):
        return self.server_id

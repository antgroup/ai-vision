from django.db import models

from .space import Space
from .user import generate_uuid


class SkillConfig(models.Model):
    """Static metadata of one skill, imported from its SKILL.md.

    Skill call statistics are scanned from telemetry spans, so this table only
    stores the descriptive side (name, description, path, license ...) so the
    skill overview can render references even before any call is observed.
    """

    id = models.CharField(
        default=generate_uuid,
        editable=False,
        max_length=36,
        primary_key=True,
        serialize=False,
    )
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name="skill_configs",
    )
    name = models.CharField(max_length=128)
    description = models.CharField(max_length=1024, blank=True, default="")
    path = models.CharField(max_length=1024, blank=True, default="")
    # Import origin marker, e.g. "skillmd" or "yaml"; informational only.
    source = models.CharField(max_length=64, blank=True, default="")
    # The verbatim SKILL.md (frontmatter plus body) as pasted by the user.
    raw = models.JSONField(default=dict, blank=True)
    allowed_tools = models.JSONField(default=list, blank=True)
    license = models.CharField(max_length=128, blank=True, default="")
    compatibility = models.CharField(max_length=256, blank=True, default="")
    imported_at = models.DateTimeField(auto_now_add=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_labs_skill_config"
        unique_together = ("space", "name")

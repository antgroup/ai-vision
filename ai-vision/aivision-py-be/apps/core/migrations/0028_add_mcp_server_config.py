import django.db.models.deletion
from django.db import migrations, models

import apps.core.models.user


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027merge0026agentmetadata0026space_owner"),
    ]

    operations = [
        migrations.CreateModel(
            name="McpServerConfig",
            fields=[
                (
                    "id",
                    models.CharField(
                        default=apps.core.models.user.generate_uuid,
                        editable=False,
                        max_length=36,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("server_id", models.CharField(max_length=128)),
                (
                    "description",
                    models.CharField(blank=True, default="", max_length=512),
                ),
                (
                    "transport",
                    models.CharField(blank=True, default="", max_length=16),
                ),
                (
                    "command",
                    models.CharField(blank=True, default="", max_length=512),
                ),
                ("args", models.JSONField(blank=True, null=True)),
                ("url", models.CharField(blank=True, default="", max_length=1024)),
                (
                    "env_keys",
                    models.JSONField(blank=True, default=list),
                ),
                (
                    "header_keys",
                    models.JSONField(blank=True, default=list),
                ),
                ("raw", models.JSONField(blank=True, default=dict)),
                # Full tools/list snapshot fetched through the MCP protocol.
                # Header / env values used for the fetch are request-scoped
                # and never stored here.
                ("tools", models.JSONField(blank=True, default=list)),
                (
                    "tools_synced_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("imported_at", models.DateTimeField(auto_now_add=True)),
                ("gmt_create", models.DateTimeField(auto_now_add=True)),
                ("gmt_modified", models.DateTimeField(auto_now=True)),
                ("gmt_deleted", models.DateTimeField(blank=True, null=True)),
                (
                    "space",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_server_configs",
                        to="core.space",
                    ),
                ),
            ],
            options={
                "db_table": "aivision_labs_mcp_server_config",
                "unique_together": {("space", "server_id")},
            },
        ),
    ]

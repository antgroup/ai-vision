import django.db.models.deletion
from django.db import migrations, models

import apps.core.models.user


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028merge_add_mcp_server_config_and_diagnosis_quick_run_request"),
    ]

    operations = [
        migrations.CreateModel(
            name="SkillConfig",
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
                ("name", models.CharField(max_length=128)),
                (
                    "description",
                    models.CharField(blank=True, default="", max_length=1024),
                ),
                ("path", models.CharField(blank=True, default="", max_length=1024)),
                ("source", models.CharField(blank=True, default="", max_length=64)),
                ("raw", models.JSONField(blank=True, default=dict)),
                ("allowed_tools", models.JSONField(blank=True, default=list)),
                (
                    "license",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                (
                    "compatibility",
                    models.CharField(blank=True, default="", max_length=256),
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
                        related_name="skill_configs",
                        to="core.space",
                    ),
                ),
            ],
            options={
                "db_table": "aivision_labs_skill_config",
                "unique_together": {("space", "name")},
            },
        ),
    ]

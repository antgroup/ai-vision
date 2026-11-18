from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_rename_organization_control_plane_to_platform"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterModelOptions(
                    name="agentdashboardpanel",
                    options={
                        "ordering": ["page", "group_name", "sort_order"],
                        "verbose_name": "[废弃] Agent Dashboard Panel",
                        "verbose_name_plural": "[废弃] Agent Dashboard Panels",
                    },
                ),
                migrations.AlterModelOptions(
                    name="apikey",
                    options={
                        "ordering": ["-gmt_create"],
                        "verbose_name": "[废弃] Api Key",
                        "verbose_name_plural": "[废弃] Api Keys",
                    },
                ),
                migrations.AlterModelOptions(
                    name="session",
                    options={
                        "ordering": ["-gmt_create"],
                        "verbose_name": "[废弃] Session",
                        "verbose_name_plural": "[废弃] Sessions",
                    },
                ),
                migrations.AlterModelOptions(
                    name="spacedatasourcebinding",
                    options={
                        "ordering": [
                            "space_observation_project",
                            "datasource_type",
                        ],
                        "verbose_name": "[废弃] Space Datasource Binding",
                        "verbose_name_plural": "[废弃] Space Datasource Bindings",
                    },
                ),
                migrations.AlterModelOptions(
                    name="spaceobservationcredential",
                    options={
                        "ordering": ["-gmt_create"],
                        "verbose_name": "Space Observation Credential",
                        "verbose_name_plural": "Space Observation Credentials",
                    },
                ),
                migrations.AlterModelOptions(
                    name="userremotemapping",
                    options={
                        "ordering": ["-gmt_create"],
                        "verbose_name": "[废弃] User Remote Mapping",
                        "verbose_name_plural": "[废弃] User Remote Mappings",
                    },
                ),
                migrations.AlterField(
                    model_name="agent",
                    name="status",
                    field=models.SmallIntegerField(
                        choices=[
                            (0, "Disabled"),
                            (1, "Normal"),
                            (2, "Pending"),
                        ],
                        default=1,
                    ),
                ),
                migrations.AlterField(
                    model_name="agenttypeconfig",
                    name="category",
                    field=models.CharField(
                        choices=[
                            ("BUILT_IN", "蚂蚁生态Agent"),
                            ("CUSTOM", "第三方Agent"),
                        ],
                        default="BUILT_IN",
                        max_length=32,
                    ),
                ),
                migrations.AlterField(
                    model_name="agenttypeconfig",
                    name="config_guide",
                    field=models.JSONField(blank=True, null=True),
                ),
                migrations.AlterField(
                    model_name="agenttypeconfig",
                    name="setup_mode",
                    field=models.CharField(
                        choices=[
                            ("AUTO", "自动发现"),
                            ("CLI", "CLI接入"),
                            ("APIKEY", "APIKey接入"),
                            ("LANGFUSE", "Langfuse接入"),
                            ("OTHER", "其他方式"),
                        ],
                        default="AUTO",
                        max_length=16,
                    ),
                ),
                migrations.AlterField(
                    model_name="datasource",
                    name="source_system",
                    field=models.CharField(
                        choices=[
                            ("langfuse", "Langfuse"),
                            ("ceresdb", "CeresDB"),
                            ("stargazer", "Stargazer"),
                            ("stargazer_native", "StargazerNative"),
                        ],
                        max_length=32,
                    ),
                ),
                migrations.AlterField(
                    model_name="spacedatasourcebinding",
                    name="source_system",
                    field=models.CharField(
                        choices=[
                            ("langfuse", "Langfuse"),
                            ("ceresdb", "CeresDB"),
                            ("stargazer", "Stargazer"),
                            ("stargazer_native", "StargazerNative"),
                        ],
                        max_length=32,
                    ),
                ),
            ],
        ),
    ]

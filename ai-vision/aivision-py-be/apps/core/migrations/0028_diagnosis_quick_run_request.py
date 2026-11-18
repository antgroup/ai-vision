import django.db.models.deletion
from django.db import migrations, models

import apps.core.models.diagnosis_quick_run


class Migration(migrations.Migration):
    dependencies = [("core", "0027merge0026agentmetadata0026space_owner")]
    operations = [
        migrations.CreateModel(
            name="DiagnosisQuickRunRequest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "uuid",
                    models.CharField(
                        default=apps.core.models.diagnosis_quick_run.owned_uuid,
                        max_length=32,
                        unique=True,
                    ),
                ),
                ("request_id", models.CharField(max_length=36)),
                ("canonical_input", models.JSONField()),
                ("input_digest", models.CharField(max_length=64)),
                ("resolved_plan", models.JSONField()),
                ("plan_digest", models.CharField(max_length=64)),
                *[
                    (
                        name,
                        models.CharField(
                            default=apps.core.models.diagnosis_quick_run.owned_uuid,
                            max_length=32,
                            unique=True,
                        ),
                    )
                    for name in ("dataset_uuid", "job_uuid", "task_uuid")
                ],
                (
                    "provisioning_step",
                    models.CharField(default="prepared", max_length=24),
                ),
                ("lease_epoch", models.PositiveBigIntegerField(default=0)),
                ("lease_until", models.DateTimeField(null=True)),
                ("safe_error_code", models.CharField(default="", max_length=64)),
                ("gmt_create", models.DateTimeField(auto_now_add=True)),
                ("gmt_modified", models.DateTimeField(auto_now=True)),
                ("gmt_deleted", models.DateTimeField(null=True)),
                *[
                    (
                        name,
                        models.ForeignKey(
                            db_constraint=False,
                            on_delete=django.db.models.deletion.PROTECT,
                            to="core." + name,
                        ),
                    )
                    for name in ("space", "user", "agent")
                ],
            ],
            options={
                "db_table": "aivision_diagnosis_quick_run_request",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("space", "user", "request_id"),
                        name="uk_quick_space_user_request",
                    )
                ],
            },
        )
    ]

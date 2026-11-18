import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_agenttypeconfig_default_trace_query_source_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="agenttypeconfig",
            name="runtime_type",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="agenttypeconfig",
            name="display_name",
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name="agenttypeconfig",
            name="display_scope",
            field=models.CharField(
                choices=[
                    ("PERSONAL", "个人空间"),
                    ("BUSINESS", "业务空间"),
                    ("ALL", "全部空间"),
                    ("HIDDEN", "隐藏"),
                ],
                default="PERSONAL",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="agent",
            name="agent_type",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="agents",
                to="core.agenttypeconfig",
            ),
        ),
    ]

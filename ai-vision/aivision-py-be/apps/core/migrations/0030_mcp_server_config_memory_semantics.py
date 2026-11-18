from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_add_skill_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpserverconfig",
            name="memory_semantics",
            field=models.JSONField(blank=True, null=True),
        ),
    ]

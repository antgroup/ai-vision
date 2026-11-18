from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0025_rename_cli_to_aiv"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

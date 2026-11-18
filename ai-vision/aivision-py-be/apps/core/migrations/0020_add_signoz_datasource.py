from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_restore_platform_agent_identity"),
    ]

    operations = [
        migrations.AlterField(
            model_name="datasource",
            name="source_system",
            field=models.CharField(
                choices=[
                    ("langfuse", "Langfuse"),
                    ("ceresdb", "CeresDB"),
                    ("stargazer", "Stargazer"),
                    ("stargazer_native", "StargazerNative"),
                    ("signoz", "SigNoz"),
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
                    ("signoz", "SigNoz"),
                ],
                max_length=32,
            ),
        ),
    ]

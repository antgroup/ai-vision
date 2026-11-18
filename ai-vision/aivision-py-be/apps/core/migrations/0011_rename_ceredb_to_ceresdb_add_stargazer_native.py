from django.db import migrations, models


def rename_ceredb_to_ceresdb(apps, schema_editor):
    DataSource = apps.get_model("core", "DataSource")
    DataSource.objects.filter(source_system="ceredb").update(source_system="ceresdb")

    SpaceDatasourceBinding = apps.get_model("core", "SpaceDatasourceBinding")
    SpaceDatasourceBinding.objects.filter(source_system="ceredb").update(
        source_system="ceresdb"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_spacemember_role_default_change"),
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
        migrations.RunPython(
            rename_ceredb_to_ceresdb,
            migrations.RunPython.noop,
        ),
    ]

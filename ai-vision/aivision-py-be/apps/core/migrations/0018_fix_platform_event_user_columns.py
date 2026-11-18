from django.db import migrations

OLD_USER_COLUMN = "resource.ant.username"
NEW_USER_COLUMN = "attributes.ant.username"
RUNTIMES = ("openclaw", "opencode", "pi", "other")


def fix_platform_event_user_columns(apps, schema_editor):
    DataSource = apps.get_model("core", "DataSource")
    datasources = DataSource.objects.filter(
        datasource_type="logs",
        source_system="stargazer_native",
        agent_type__platform__isnull=False,
        agent_type__runtime_type__in=RUNTIMES,
    )

    for datasource in datasources.iterator():
        if not isinstance(datasource.config, dict):
            continue
        if datasource.config.get("user_column") != OLD_USER_COLUMN:
            continue
        datasource.config = datasource.config | {"user_column": NEW_USER_COLUMN}
        datasource.save(update_fields=["config"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_preserve_digits_in_platform_event_table_names"),
    ]

    operations = [
        migrations.RunPython(
            fix_platform_event_user_columns,
            migrations.RunPython.noop,
        ),
    ]

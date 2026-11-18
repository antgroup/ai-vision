from django.db import migrations, models


def add_profile_extra_column_if_missing(apps, schema_editor):
    table_name = "aivision_user"
    column_name = "profile_extra"
    existing_columns = {
        column.name
        for column in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(), table_name
        )
    }

    if column_name in existing_columns:
        return

    User = apps.get_model("core", "User")
    field = models.TextField(blank=True, null=True)
    field.set_attributes_from_name(column_name)
    schema_editor.add_field(User, field)


def remove_profile_extra_column_if_exists(apps, schema_editor):
    table_name = "aivision_user"
    column_name = "profile_extra"
    existing_columns = {
        column.name
        for column in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(), table_name
        )
    }

    if column_name not in existing_columns:
        return

    User = apps.get_model("core", "User")
    field = models.TextField(blank=True, null=True)
    field.set_attributes_from_name(column_name)
    schema_editor.remove_field(User, field)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_space_agent_product_fields"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    add_profile_extra_column_if_missing,
                    remove_profile_extra_column_if_exists,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="user",
                    name="profile_extra",
                    field=models.TextField(blank=True, null=True),
                ),
            ],
        ),
    ]

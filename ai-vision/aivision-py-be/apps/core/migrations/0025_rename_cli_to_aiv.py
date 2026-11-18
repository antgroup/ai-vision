"""Update saved installation instructions without replacing Agent configuration."""

import re

from django.db import migrations


def rename_cli(apps, schema_editor, old_name, new_name):
    pattern = re.compile(r"(?<![\w-])" + re.escape(old_name) + r"(?![\w-])")

    def rewrite(value):
        if isinstance(value, str):
            return pattern.sub(new_name, value)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    configs = apps.get_model("core", "AgentTypeConfig").objects.using(
        schema_editor.connection.alias
    )
    for config in configs.only("pk", "cli_command", "config_guide").iterator():
        changes = {}
        for field in ("cli_command", "config_guide"):
            previous = getattr(config, field)
            updated = rewrite(previous)
            if updated != previous:
                changes[field] = updated
        if changes:
            configs.filter(pk=config.pk).update(**changes)


def forwards(apps, schema_editor):
    rename_cli(apps, schema_editor, "aivforge", "aiv")


def backwards(apps, schema_editor):
    rename_cli(apps, schema_editor, "aiv", "aivforge")


class Migration(migrations.Migration):
    dependencies = [("core", "0024_neutral_agent_category")]

    operations = [migrations.RunPython(forwards, backwards)]

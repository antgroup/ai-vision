import re

from django.db import migrations


def _event_table_names(platform_name, space_name, agent_product):
    product = str(agent_product or "").lower()
    event_product_max_length = 64 - len("_log")
    old_product = re.sub(r"[^a-z]", "", product)[:event_product_max_length]
    corrected_product = re.sub(r"[^a-z0-9]", "", product)[
        :event_product_max_length
    ]
    if not old_product or old_product == corrected_product:
        return None

    namespace = f"{platform_name}_{space_name}"
    return (
        f"{namespace}__{old_product}_log",
        f"{namespace}__{corrected_product}_log",
    )


def _rewrite_event_table_names(apps, *, reverse=False):
    Agent = apps.get_model("core", "Agent")
    DataSource = apps.get_model("core", "DataSource")

    datasources = DataSource.objects.filter(
        datasource_type="logs",
        source_system="stargazer_native",
        agent_type__platform__isnull=False,
    ).select_related("agent_type", "agent_type__platform")

    for datasource in datasources.iterator():
        agents = list(
            Agent.objects.filter(agent_type_id=datasource.agent_type_id)
            .select_related("space")
            .order_by("id")[:2]
        )
        if len(agents) != 1:
            continue

        agent = agents[0]
        space = agent.space
        platform = datasource.agent_type.platform
        if not space.platform_space_name or space.platform_id != platform.id:
            continue

        table_names = _event_table_names(
            platform.name,
            space.platform_space_name,
            datasource.agent_type.agent_product,
        )
        if table_names is None or not isinstance(datasource.config, dict):
            continue

        old_table_name, corrected_table_name = table_names
        expected, replacement = (
            (corrected_table_name, old_table_name)
            if reverse
            else (old_table_name, corrected_table_name)
        )
        if datasource.config.get("table_name") != expected:
            continue

        datasource.config = datasource.config | {"table_name": replacement}
        datasource.save(update_fields=["config"])


def preserve_digits(apps, schema_editor):
    _rewrite_event_table_names(apps)


def remove_preserved_digits(apps, schema_editor):
    _rewrite_event_table_names(apps, reverse=True)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_sync_current_model_state"),
    ]

    operations = [
        migrations.RunPython(preserve_digits, remove_preserved_digits),
    ]

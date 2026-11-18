from django.db import migrations


class Migration(migrations.Migration):
    """Merge parallel 0026 migrations (agent.metadata / space.owner)."""

    dependencies = [
        ("core", "0026_agent_metadata"),
        ("core", "0026_space_owner"),
    ]

    operations = []
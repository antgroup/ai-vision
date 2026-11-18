from django.db import migrations


class Migration(migrations.Migration):
    """Merge parallel migrations (mcp.memory_semantics / model.exchange_rates)."""

    dependencies = [
        ("core", "0030_mcp_server_config_memory_semantics"),
        ("core", "0033_model_exchange_rates"),
    ]

    operations = []

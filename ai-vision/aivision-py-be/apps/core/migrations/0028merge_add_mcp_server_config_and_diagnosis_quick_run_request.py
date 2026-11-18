from django.db import migrations


class Migration(migrations.Migration):
    """Merge parallel 0028 migrations (mcp.server_config / diagnosis.quick_run_request)."""

    dependencies = [
        ("core", "0028_add_mcp_server_config"),
        ("core", "0028_diagnosis_quick_run_request"),
    ]

    operations = []
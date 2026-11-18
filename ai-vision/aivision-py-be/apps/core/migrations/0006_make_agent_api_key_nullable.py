from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_observationendpointconfig_space_billing_owner_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="agent",
            name="api_key",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="agents",
                to="core.apikey",
            ),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_sandbox_bootstrap_capabilities"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="product_guide_pending",
            field=models.BooleanField(default=False),
        ),
    ]

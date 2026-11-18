import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0037_user_product_guide_pending")]

    operations = [
        migrations.AddField(
            model_name="spacekey",
            name="onboarding_agent",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="core.agent",
            ),
        ),
        migrations.AddField(
            model_name="spacekey",
            name="onboarding_ciphertext",
            field=models.TextField(blank=True, editable=False, null=True),
        ),
    ]

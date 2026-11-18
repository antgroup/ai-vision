from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_user_profile_extra"),
    ]

    operations = [
        migrations.AlterField(
            model_name="spacemember",
            name="role",
            field=models.SmallIntegerField(
                choices=[(1, "Admin"), (2, "Developer"), (3, "Member")],
                default=3,
            ),
        ),
    ]

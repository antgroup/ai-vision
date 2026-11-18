from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_add_space_operation_log"),
    ]

    operations = [
        migrations.CreateModel(
            name="SpaceKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key_prefix", models.CharField(max_length=20)),
                ("key_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("name", models.CharField(blank=True, default="", max_length=128)),
                ("enabled", models.BooleanField(default=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("gmt_create", models.DateTimeField(auto_now_add=True)),
                ("gmt_modified", models.DateTimeField(auto_now=True)),
                ("gmt_deleted", models.DateTimeField(blank=True, null=True)),
                ("space", models.ForeignKey(
                    db_constraint=False,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="space_keys",
                    to="core.space",
                )),
                ("creator", models.ForeignKey(
                    blank=True,
                    db_constraint=False,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_space_keys",
                    to="core.user",
                )),
            ],
            options={
                "db_table": "aivision_space_key",
                "ordering": ["-gmt_create"],
            },
        ),

    ]

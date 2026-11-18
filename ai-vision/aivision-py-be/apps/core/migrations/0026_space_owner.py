from django.db import migrations, models
import django.db.models.deletion


def backfill_owners(apps, schema_editor):
    Space = apps.get_model("core", "Space")
    Member = apps.get_model("core", "SpaceMember")
    alias = schema_editor.connection.alias
    unresolved = []
    for space in (
        Space.objects.using(alias).filter(type=2, owner__isnull=True).iterator()
    ):
        if (
            Member.objects.using(alias)
            .filter(
                space_id=space.pk,
                user_id=space.creator_id,
                role=1,
                gmt_deleted__isnull=True,
                user__status=1,
                user__gmt_deleted__isnull=True,
            )
            .exists()
        ):
            Space.objects.using(alias).filter(pk=space.pk).update(
                owner_id=space.creator_id
            )
        elif space.status == 1 and space.gmt_deleted is None:
            unresolved.append(space.uuid)
    if unresolved:
        print(
            "Business spaces requiring explicit owner assignment: "
            + ", ".join(unresolved)
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0025_rename_cli_to_aiv")]
    operations = [
        migrations.AddField(
            model_name="space",
            name="owner",
            field=models.ForeignKey(
                to="core.user",
                null=True,
                blank=True,
                db_constraint=False,
                related_name="owned_spaces",
                on_delete=django.db.models.deletion.PROTECT,
            ),
        ),
        migrations.RunPython(backfill_owners, migrations.RunPython.noop),
    ]

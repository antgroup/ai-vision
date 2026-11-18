"""Resolve legacy spaces explicitly; never pick an arbitrary administrator."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Space, SpaceMember, SpaceOperationLog
from apps.core.services.operation_log_service import log_operation


class Command(BaseCommand):
    help = (
        "List unresolved business spaces, or assign a reviewed existing Admin as owner"
    )

    def add_arguments(self, parser):
        parser.add_argument("--space", help="Business space UUID")
        parser.add_argument("--user", help="Reviewed active Admin user UUID")

    def handle(self, *args, **options):
        space_uuid, user_uuid = options["space"], options["user"]
        if not space_uuid and not user_uuid:
            for space in Space.objects.filter(
                type=2, status=1, gmt_deleted__isnull=True, owner__isnull=True
            ):
                self.stdout.write(f"{space.uuid} {space.name}")
            return
        if not space_uuid or not user_uuid:
            raise CommandError(
                "Specify both --space and --user after reviewing ownership"
            )
        with transaction.atomic():
            space = (
                Space.objects.select_for_update()
                .filter(
                    uuid=space_uuid,
                    type=2,
                    status=1,
                    gmt_deleted__isnull=True,
                )
                .first()
            )
            if space is None:
                raise CommandError("Active business space not found")
            target = (
                SpaceMember.objects.select_for_update()
                .select_related("user")
                .filter(
                    space=space,
                    user__uuid=user_uuid,
                    role=1,
                    gmt_deleted__isnull=True,
                    user__status=1,
                    user__gmt_deleted__isnull=True,
                )
                .first()
            )
            if target is None:
                raise CommandError(
                    "Target must already be an active Admin in this space"
                )
            if space.owner_id:
                if space.owner_id == target.user_id:
                    self.stdout.write("Owner already assigned")
                    return
                raise CommandError(
                    "Owner already assigned; use the ownership transfer API"
                )
            space.owner = target.user
            space.save(update_fields=["owner", "gmt_modified"])
            log_operation(
                space=space,
                operator=target.user,
                category=SpaceOperationLog.CATEGORY_MEMBER_CHANGE,
                action="assign_owner",
                target_display=target.user.name,
                detail=f"Deployment maintenance assigned Owner: {target.user.uuid}",
                extra={"source": "assign_space_owner", "owner_uuid": target.user.uuid},
            )
        self.stdout.write("Owner assigned")

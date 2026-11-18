"""Ensure the ``system`` PlatformConfig row exists.

The config-cache in :mod:`apps.core.cache` invalidates across processes by
polling ``aivision_platform_config(platform_code='system').config.cache_version``
and reads its full-refresh period from ``config.cache_ttl``. A freshly seeded
database has no such row, so the post_save ``_bump_db_version`` UPDATE matches
0 rows and cross-process invalidation silently degrades to the 86400s default —
catalog edits then look "stuck" until the server is restarted. This command
idempotently provisions that row so edits propagate within the poll interval.
"""

from django.core.management.base import BaseCommand

from apps.core.models import PlatformConfig


class Command(BaseCommand):
    help = "Idempotently ensure the 'system' PlatformConfig cache row exists."

    def add_arguments(self, parser):
        parser.add_argument(
            "--cache-ttl",
            type=int,
            default=None,
            help="Set cache TTL explicitly; otherwise preserve it (new configs: 3600).",
        )

    def handle(self, *args, **options):
        requested_ttl = options["cache_ttl"]
        ttl = requested_ttl if requested_ttl is not None else 3600
        pc = PlatformConfig.objects.filter(platform_code="system").first()
        if pc is None:
            pc = PlatformConfig.objects.create(
                platform_code="system",
                platform_name="System",
                status=1,
                config={"cache_version": 0, "cache_ttl": ttl},
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created system PlatformConfig id={pc.id} "
                    f"cache_version=0 cache_ttl={ttl}"
                )
            )
            return

        config = dict(pc.config or {})
        changed = False
        if "cache_version" not in config:
            config["cache_version"] = 0
            changed = True
        if "cache_ttl" not in config or (
            requested_ttl is not None and config["cache_ttl"] != requested_ttl
        ):
            config["cache_ttl"] = ttl
            changed = True
        if changed:
            pc.config = config
            pc.save(update_fields=["config"])
            self.stdout.write(
                self.style.SUCCESS(
                    f"Updated system PlatformConfig id={pc.id} "
                    f"cache_version={config['cache_version']} cache_ttl={config['cache_ttl']}"
                )
            )
        else:
            self.stdout.write(
                f"system PlatformConfig already correct "
                f"(id={pc.id} cache_version={config['cache_version']} "
                f"cache_ttl={config['cache_ttl']})"
            )

"""Bootstrap the AI Vision <-> SigNoz integration (produce the deployment org).

Runs the idempotent sequence in :mod:`apps.core.services.signoz_integration`:
wait for signoz, register the root user on a fresh deployment (which *produces*
the single org — never hardcoded), mint an internal service-account key, point
the signoz DataSource routes at it and persist the org for ingest-token stamping.

The minted SIGNOZ-API-KEY is deployment-internal: it is stored only in the signoz
DataSource config and is NEVER printed here. Use ``signoz_integration_status
--print-org`` to read back the (non-sensitive) org id.

Typical use (local forge stack, invoked from the be container's postStart hook)::

    python manage.py bootstrap_signoz_integration
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.core.services import signoz_integration as si


class Command(BaseCommand):
    help = (
        "Register/sign in to SigNoz, mint the internal key, configure the signoz "
        "datasource routes and record the deployment org (idempotent)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--endpoint",
            help="SigNoz base URL (default: SIGNOZ_INTERNAL_URL)",
        )
        parser.add_argument(
            "--query-endpoint",
            help=(
                "endpoint stamped into the signoz DataSource routes "
                "(default: AIVISION_SIGNOZ_QUERY_ENDPOINT, else --endpoint)"
            ),
        )
        parser.add_argument("--email", help="root/admin email (default: SIGNOZ_ROOT_EMAIL)")
        parser.add_argument(
            "--password",
            help="root/admin password (default: SIGNOZ_USER_ROOT_PASSWORD)",
        )
        parser.add_argument(
            "--org-name", help="org display name used on registration"
        )
        parser.add_argument(
            "--org-id",
            help="existing org id (only needed when signoz is already registered)",
        )
        parser.add_argument(
            "--role",
            default=si.DEFAULT_ROLE_NAME,
            help=f"signoz role to bind (default: {si.DEFAULT_ROLE_NAME})",
        )
        parser.add_argument(
            "--no-make-default",
            action="store_true",
            help="do not make signoz the default route for the three signal types",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="re-run even if already configured (mints a new key)",
        )
        parser.add_argument(
            "--ready-timeout",
            type=int,
            default=si.DEFAULT_READY_TIMEOUT,
            help="seconds to wait for signoz readiness",
        )
        parser.add_argument(
            "--poll-interval",
            type=int,
            default=si.DEFAULT_POLL_INTERVAL,
            help="seconds between readiness probes",
        )

    def handle(self, *args, **options):
        def log(msg, *a):
            self.stdout.write(msg % a if a else msg)

        try:
            result = si.bootstrap(
                endpoint=options["endpoint"],
                query_endpoint=options["query_endpoint"],
                email=options["email"],
                password=options["password"],
                org_name=options["org_name"],
                org_id=options["org_id"],
                role_name=options["role"],
                make_default=not options["no_make_default"],
                force=options["force"],
                ready_timeout=options["ready_timeout"],
                poll_interval=options["poll_interval"],
                log=log,
            )
        except si.SigNozIntegrationError as exc:
            raise CommandError(str(exc)) from exc

        if result.reused:
            self.stdout.write(
                self.style.SUCCESS(
                    f"SigNoz integration already configured (org={result.organization_id})"
                )
            )
            return

        # Deliberately omit the API key from all output.
        self.stdout.write(
            self.style.SUCCESS(
                "SigNoz integration ready: org={org} service_account={sa} "
                "query_endpoint={ep} datasources={n} product(s)".format(
                    org=result.organization_id,
                    sa=result.service_account_id,
                    ep=result.query_endpoint,
                    n=len(result.agent_products),
                )
            )
        )

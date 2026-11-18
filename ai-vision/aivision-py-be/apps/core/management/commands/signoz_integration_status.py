"""Report the AI Vision <-> SigNoz integration state (read-only).

Companion to :mod:`bootstrap_signoz_integration`. The integration produces a
single deployment-level SigNoz ``organization_id``; this command reads it back so
the cast engine can discover it and inject it into downstream components (e.g.
``agentlens``'s ``SIGNOZ_ORG_ID``).

Security boundary: the minted ``SIGNOZ-API-KEY`` is deployment-internal and lives
only in the signoz DataSource config. This command NEVER prints it. ``--print-org``
emits the bare, non-sensitive org id on stdout (and nothing else) so a caller can
capture it directly::

    ORG_ID=$(python manage.py signoz_integration_status --print-org)
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.core.services import signoz_integration as si


class Command(BaseCommand):
    help = (
        "Show the SigNoz integration state; --print-org emits only the "
        "(non-sensitive) org id for the cast engine to discover."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--print-org",
            action="store_true",
            help="print only the org id on stdout (empty + exit 1 if not bootstrapped)",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="print the full non-sensitive state as JSON",
        )
        parser.add_argument(
            "--require",
            action="store_true",
            help="exit non-zero when the integration is not yet configured",
        )

    def handle(self, *args, **options):
        state = si.stored_state()
        org_id = str(state.get("organization_id") or "").strip()
        configured = bool(state.get("configured")) and bool(org_id)

        if options["print_org"]:
            # Bare stdout for capture. Absence is signalled by exit code, not text.
            if options["require"] and not configured:
                raise CommandError("SigNoz integration is not bootstrapped")
            if not org_id:
                raise CommandError(
                    "no org id recorded; run bootstrap_signoz_integration first"
                )
            self.stdout.write(org_id)
            return

        if options["json"]:
            payload = {
                "configured": configured,
                "organization_id": org_id,
                "endpoint": state.get("endpoint", ""),
                "query_endpoint": state.get("query_endpoint", ""),
                "service_account_id": state.get("service_account_id", ""),
                "role_id": state.get("role_id", ""),
                "registered": bool(state.get("registered", False)),
                "agent_products": list(state.get("agent_products", [])),
                "bootstrapped_at": state.get("bootstrapped_at", ""),
            }
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
            if options["require"] and not configured:
                raise CommandError("SigNoz integration is not bootstrapped")
            return

        if not configured:
            message = "SigNoz integration is not bootstrapped yet"
            if options["require"]:
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(message + "; run bootstrap_signoz_integration"))
            return

        self.stdout.write(
            self.style.SUCCESS(
                "SigNoz integration configured\n"
                "  org_id            : {org}\n"
                "  endpoint          : {ep}\n"
                "  query_endpoint    : {qep}\n"
                "  service_account   : {sa}\n"
                "  registered here   : {reg}\n"
                "  agent products    : {prods}\n"
                "  bootstrapped at   : {at}".format(
                    org=org_id,
                    ep=state.get("endpoint", ""),
                    qep=state.get("query_endpoint", ""),
                    sa=state.get("service_account_id", ""),
                    reg="yes" if state.get("registered") else "no",
                    prods=", ".join(state.get("agent_products", [])) or "(none)",
                    at=state.get("bootstrapped_at", ""),
                )
            )
        )

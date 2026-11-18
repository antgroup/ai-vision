"""Configure the OceanBase-backed SigNoz data plane for one agent product."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.adapters.signoz_metrics import migrate_legacy_metrics_queries
from apps.core.models import AgentTypeConfig, DataSource


def _read_api_key(endpoint: str) -> str:
    """Reuse the deployment's query key when a catalog adds another product.

    Bootstrap stores its internal service-account key only in datasource
    configs. A later catalog update runs without that temporary environment
    variable, so recover it from active routes for this exact query endpoint.
    Explicit credentials still win for bootstrap and deliberate key rotation.
    """
    explicit = os.environ.get("SIGNOZ_API_KEY", "").strip()
    if explicit:
        return explicit

    keys = set()
    configs = DataSource.objects.filter(
        source_system="signoz",
        enabled=1,
        gmt_deleted__isnull=True,
        agent_type__status=1,
        agent_type__gmt_deleted__isnull=True,
    ).values_list("config", flat=True)
    for config in configs:
        if not isinstance(config, dict):
            continue
        route_endpoint = config.get("endpoint")
        key = config.get("api_key")
        if not isinstance(route_endpoint, str) or not isinstance(key, str):
            continue
        if route_endpoint.rstrip("/") == endpoint and key.strip():
            keys.add(key.strip())
    if len(keys) > 1:
        raise CommandError(
            "Multiple SigNoz API keys exist for this endpoint; "
            "set SIGNOZ_API_KEY explicitly"
        )
    return next(iter(keys), "")


def _validate_endpoint(value: str) -> str:
    endpoint = value.rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CommandError("--endpoint must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise CommandError("--endpoint must not contain credentials")
    return endpoint


class Command(BaseCommand):
    help = "Create/update SigNoz traces, logs and metrics datasource routes"

    def add_arguments(self, parser):
        parser.add_argument("--agent-product", default="Codex")
        parser.add_argument("--endpoint", required=True)
        parser.add_argument("--agent-product-filter")
        parser.add_argument(
            "--display-scope",
            choices=("PERSONAL", "BUSINESS", "ALL"),
            help="Optionally align the AI Vision dashboard scopes exposed for this agent",
        )
        parser.add_argument(
            "--make-default",
            action="store_true",
            help="Make SigNoz the default route for all three signal types",
        )
        parser.add_argument(
            "--replace-existing",
            action="store_true",
            help="Make SigNoz default while retaining other configured observation sources",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        agent_product = options["agent_product"].strip()
        if not agent_product:
            raise CommandError("--agent-product must not be empty")
        endpoint = _validate_endpoint(options["endpoint"])
        product_filter = (options.get("agent_product_filter") or agent_product).strip()
        api_key = _read_api_key(endpoint)

        matches = list(
            AgentTypeConfig.objects.filter(
                agent_product=agent_product,
                status=1,
                gmt_deleted__isnull=True,
            ).order_by("id")[:2]
        )
        if len(matches) != 1:
            raise CommandError(
                f"Expected exactly one active AgentTypeConfig for {agent_product!r}; "
                f"found {len(matches)}"
            )
        agent_type = matches[0]
        display_scope = options.get("display_scope")
        if display_scope and agent_type.display_scope != display_scope:
            agent_type.display_scope = display_scope
            agent_type.save(update_fields=["display_scope", "gmt_modified"])

        common_config = {
            "endpoint": endpoint,
            "agent_product_filter": product_filter,
        }
        if api_key:
            common_config["api_key"] = api_key

        for datasource_type in ("traces", "logs", "metrics"):
            competing = DataSource.objects.filter(
                agent_type=agent_type,
                datasource_type=datasource_type,
                gmt_deleted__isnull=True,
            ).exclude(source_system="signoz")
            # Materialize before disabling routes so a legacy metrics query map can
            # be migrated into the new SigNoz route on the first configuration run.
            competing_routes = list(competing.order_by("-is_default", "-enabled", "id"))
            if options["replace_existing"] or options["make_default"]:
                competing.update(is_default=0)

            existing = DataSource.objects.filter(
                agent_type=agent_type,
                datasource_type=datasource_type,
                source_system="signoz",
            ).first()
            config = dict(existing.config or {}) if existing else {}
            old_endpoint = config.get("endpoint")
            if not isinstance(old_endpoint, str) or old_endpoint.rstrip("/") != endpoint:
                # Credentials belong to their query endpoint. A replacement
                # key must be explicit or recovered for the new endpoint.
                config.pop("api_key", None)
            config.update(common_config)
            config["response_format"] = (
                "prometheus" if datasource_type == "metrics" else "table"
            )

            if datasource_type in ("traces", "logs"):
                config.pop("queries", None)

            if datasource_type == "metrics":
                queries = config.get("queries")
                if not isinstance(queries, dict) or not queries:
                    for candidate in competing_routes:
                        candidate_queries = (candidate.config or {}).get("queries")
                        if isinstance(candidate_queries, dict) and candidate_queries:
                            queries = candidate_queries
                            break
                if isinstance(queries, dict) and queries:
                    config["queries"] = migrate_legacy_metrics_queries(queries)

            datasource, created = DataSource.objects.update_or_create(
                agent_type=agent_type,
                datasource_type=datasource_type,
                source_system="signoz",
                defaults={
                    "enabled": 1,
                    "is_default": 1
                    if options["make_default"] or options["replace_existing"]
                    else (existing.is_default if existing else 0),
                    "config": config,
                    "gmt_deleted": None,
                },
            )
            action = "created" if created else "updated"
            self.stdout.write(
                f"{action} {datasource_type}/signoz datasource id={datasource.id}"
            )

        self.stdout.write(self.style.SUCCESS("SigNoz datasource routes are ready"))

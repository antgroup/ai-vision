"""Maintain canonical query summaries without changing the raw Span source."""
import json
import time

from apps.core.services.model_pricing import get_space_model_prices
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Space
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.signoz.read_model_projection import ProjectionDeferred, project_batch
from apps.core.trace_query.types import TraceListQuery, TraceQueryServiceError


class Command(BaseCommand):
    help = "Backfill/refresh versioned SigNoz Trace summaries. Does not delete or rewrite raw spans."

    def add_arguments(self, parser):
        parser.add_argument("--space", required=True, help="Existing Space UUID")
        parser.add_argument("--agent-product", required=True, help="Existing Agent routing product, not a storage predicate")
        parser.add_argument("--limit", type=int, default=100, help="Maximum traces per iteration (1..500)")
        parser.add_argument("--interval", type=float, default=2.0, help="Seconds between iterations")
        parser.add_argument("--once", action="store_true", help="Run one bounded iteration and exit")
        parser.add_argument("--backfill", action="store_true", help="Enroll one resumable historical page per iteration")
        parser.add_argument("--after-trace-id", default="", help="Resume dirty scan cursor")
        parser.add_argument("--max-batches", type=int, default=0, help="Bound iterations; 0 repeats until interrupted")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 500 or not 0.1 <= options["interval"] <= 3600 or options["max_batches"] < 0:
            raise CommandError("Invalid limit, interval or max-batches")
        try:
            space = Space.objects.get(uuid=options["space"], gmt_deleted__isnull=True)
        except (Space.DoesNotExist, ValueError) as exc:
            raise CommandError("Space not found") from exc
        query = TraceListQuery(space, None, {"agent_product": options["agent_product"]})
        client, _ = SignozTraceProvider()._context(query)
        cursor = options["after_trace_id"]
        count = 0
        try:
            while True:
                result = project_batch(client, space_id=str(space.uuid), prices=get_space_model_prices(space),
                                       limit=options["limit"], after_trace_id=cursor, backfill=options["backfill"])
                self.stdout.write(json.dumps(result, sort_keys=True))
                cursor = result["afterTraceId"]
                count += 1
                if options["once"] or options["max_batches"] and count >= options["max_batches"]:
                    return
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("Projector stopped; dirty state retained for resume.")
        except (ProjectionDeferred, TraceQueryServiceError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

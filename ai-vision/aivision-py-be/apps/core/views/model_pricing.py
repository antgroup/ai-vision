"""Space-authorized pricing; catalog refresh affects shared public metadata only."""
from django.db import transaction
from django.utils import timezone
from requests import ConnectionError, RequestException, Timeout
from rest_framework.views import APIView
from urllib3.exceptions import ReadTimeoutError

from apps.core.models import ModelPricingRule, Space
from apps.core.permissions.constants import MODEL_PRICING_READ, MODEL_PRICING_MANAGE
from apps.core.permissions.space import require_space_permission
from apps.core.services import model_pricing as pricing
from apps.core.trace_query.types import TraceQueryServiceError
from .common import success_response, error_response


def _context(request, space_id, permission=MODEL_PRICING_READ):
    return require_space_permission(user=request.user, space_id=space_id, permission=permission)


class ModelPricingView(APIView):
    def get(self, request, space_id):
        ctx = _context(request, space_id)
        rules = ModelPricingRule.objects.filter(space=ctx.space, gmt_deleted__isnull=True).order_by('-gmt_create')
        return success_response({'catalog': pricing.catalog_status(), 'rules': [pricing.serialize_rule(rule) for rule in rules],
                                 'canManage': MODEL_PRICING_MANAGE in ctx.permissions})


class ModelPricingCatalogView(APIView):
    def get(self, request, space_id):
        _context(request, space_id)
        candidates = pricing.search_catalog(provider=request.query_params.get('provider', '')[:128],
            model=request.query_params.get('model', '')[:256], q=request.query_params.get('q', '')[:256],
            reference=request.query_params.get('reference') == 'true')
        return success_response({'catalog': pricing.catalog_status(), 'candidates': candidates, 'warnings': list(pricing.WARNINGS)})


class ModelPricingSyncView(APIView):
    def post(self, request, space_id):
        _context(request, space_id, MODEL_PRICING_MANAGE)
        options = request.data
        if not isinstance(options, dict) or set(options) - {'source', 'revision'}:
            return error_response('Unsupported catalog sync options')
        source, revision = options.get('source', 'auto'), options.get('revision')
        try:
            pricing.validate_sync_options(source, revision)
        except pricing.PricingError as exc:
            return error_response(str(exc))
        try:
            pricing.sync_catalog(source=source, revision=revision)
        except pricing.PricingError:
            return error_response(
                'The models.dev pricing catalog could not be refreshed. Try again later.',
                status=502,
                data={'catalog': pricing.catalog_status()},
            )
        return success_response({'catalog': pricing.catalog_status()})


class ModelPricingRuleCreateView(APIView):
    def post(self, request, space_id):
        ctx = _context(request, space_id, MODEL_PRICING_MANAGE)
        try:
            rule = pricing.create_rule(space=ctx.space, user=request.user, data=request.data)
        except pricing.PricingError as exc:
            return error_response(str(exc))
        return success_response(pricing.serialize_rule(rule))


class ModelPricingRuleDetailView(APIView):
    @transaction.atomic
    def delete(self, request, space_id, rule_id):
        ctx = _context(request, space_id, MODEL_PRICING_MANAGE)
        Space.objects.select_for_update().get(pk=ctx.space.pk)
        ctx = require_space_permission(user=request.user, space_id=space_id, permission=MODEL_PRICING_MANAGE, lock=True)
        rule = ModelPricingRule.objects.filter(space=ctx.space, uuid=rule_id, gmt_deleted__isnull=True).first()
        if rule is None:
            return error_response('Pricing rule not found', status=404)
        rule.gmt_deleted = timezone.now()
        rule.save(update_fields=['gmt_deleted'])
        return success_response({'uuid': rule.uuid, 'deleted': True})


class ModelPricingInventoryView(APIView):
    def get(self, request, space_id):
        ctx = _context(request, space_id)
        try:
            data = pricing.native_inventory(space=ctx.space, user=request.user, params=request.query_params)
        except pricing.InventoryScopeNotFound as exc:
            return error_response(str(exc), status=404)
        except pricing.PricingError as exc:
            return error_response(str(exc))
        except TraceQueryServiceError as exc:
            return error_response(exc.message, code=exc.code, status=422)
        except RequestException as exc:
            # Requests wraps streamed body read timeouts as connection errors.
            if isinstance(exc, Timeout) or (
                isinstance(exc, ConnectionError) and exc.args and isinstance(exc.args[0], ReadTimeoutError)
            ):
                return error_response('Native pricing inventory query timed out; retry shortly', code=3035, status=504)
            # Do not expose upstream URLs, credentials or database error bodies.
            return error_response('Native pricing inventory query failed; retry shortly', code=3035, status=502)
        return success_response(data)

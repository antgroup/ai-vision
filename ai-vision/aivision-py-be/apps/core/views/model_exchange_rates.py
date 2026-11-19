"""Space-authorized access to public display-currency metadata."""
from rest_framework.views import APIView

from apps.core.permissions.constants import MODEL_PRICING_READ, MODEL_PRICING_MANAGE
from apps.core.permissions.space import require_space_permission
from apps.core.services import model_exchange_rates as fx
from .common import success_response, error_response


def _status(context):
    return {**fx.exchange_rate_status(), 'canManage': MODEL_PRICING_MANAGE in context.permissions}


class ModelExchangeRateView(APIView):
    def get(self, request, space_id):
        ctx = require_space_permission(user=request.user, space_id=space_id, permission=MODEL_PRICING_READ)
        return success_response(_status(ctx))


class ModelExchangeRateSyncView(APIView):
    def post(self, request, space_id):
        ctx = require_space_permission(user=request.user, space_id=space_id, permission=MODEL_PRICING_MANAGE)
        if not isinstance(request.data, dict) or request.data:
            return error_response('Exchange-rate refresh accepts no options')
        try:
            fx.sync_exchange_rates()
        except fx.ExchangeRateError:
            return error_response(
                'ECB reference rates could not be refreshed. Try again later.',
                status=502,
                data=_status(ctx),
            )
        return success_response(_status(ctx))

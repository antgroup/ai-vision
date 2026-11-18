"""Model-pricing display currency routes."""
from django.urls import path
from apps.core.views.model_exchange_rates import ModelExchangeRateView, ModelExchangeRateSyncView

_base = 'v1/spaces/<str:space_id>/model-pricing/exchange-rates'
urlpatterns = [
    path(_base, ModelExchangeRateView.as_view(), name='model_exchange_rates'),
    path(_base + '/sync', ModelExchangeRateSyncView.as_view(), name='model_exchange_rates_sync'),
]

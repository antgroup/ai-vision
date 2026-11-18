"""Space model pricing routes."""
from django.urls import path
from apps.core.views.model_pricing import (
    ModelPricingView, ModelPricingCatalogView, ModelPricingSyncView,
    ModelPricingRuleCreateView, ModelPricingRuleDetailView, ModelPricingInventoryView,
)

_base = 'v1/spaces/<str:space_id>/model-pricing'
urlpatterns = [
    path(_base, ModelPricingView.as_view(), name='model_pricing'),
    path(_base + '/catalog', ModelPricingCatalogView.as_view(), name='model_pricing_catalog'),
    path(_base + '/sync', ModelPricingSyncView.as_view(), name='model_pricing_sync'),
    path(_base + '/rules', ModelPricingRuleCreateView.as_view(), name='model_pricing_rules'),
    path(_base + '/rules/<str:rule_id>', ModelPricingRuleDetailView.as_view(), name='model_pricing_rule'),
    path(_base + '/inventory', ModelPricingInventoryView.as_view(), name='model_pricing_inventory'),
]

"""Shared public pricing metadata and immutable, Space-owned pricing versions."""
from django.db import models as db_models
from .space import Space
from .user import User, generate_uuid


class ModelPricingCatalogSnapshot(db_models.Model):
    snapshot_id = db_models.CharField(max_length=64, primary_key=True)
    models = db_models.JSONField(default=list)
    source_type = db_models.CharField(max_length=32, default='api')
    source_url = db_models.URLField(max_length=1024, blank=True, default='https://models.dev/api.json')
    source_revision = db_models.CharField(max_length=80, blank=True, default='')
    gmt_create = db_models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'aivision_model_pricing_catalog_snapshot'


class ModelPricingCatalogState(db_models.Model):
    key = db_models.CharField(max_length=32, primary_key=True, default='models.dev')
    snapshot = db_models.ForeignKey(ModelPricingCatalogSnapshot, null=True, on_delete=db_models.PROTECT, db_constraint=False)
    fetched_at = db_models.DateTimeField(null=True)
    last_attempt_at = db_models.DateTimeField(null=True)
    last_error = db_models.CharField(max_length=256, blank=True, default='')

    class Meta:
        db_table = 'aivision_model_pricing_catalog_state'


class ModelPricingRule(db_models.Model):
    uuid = db_models.CharField(max_length=36, primary_key=True, default=generate_uuid, editable=False)
    space = db_models.ForeignKey(Space, on_delete=db_models.CASCADE, db_constraint=False, related_name='model_pricing_rules')
    creator = db_models.ForeignKey(User, on_delete=db_models.SET_NULL, null=True, db_constraint=False)
    provider = db_models.CharField(max_length=128)
    model = db_models.CharField(max_length=256)
    kind = db_models.CharField(max_length=16, choices=[('manual', 'Manual'), ('alias', 'Alias'), ('reference', 'Reference'), ('subscription', 'Subscription')])
    estimate_list_price = db_models.BooleanField(default=False)
    rates = db_models.JSONField(default=dict)
    effective_from = db_models.DateTimeField()
    catalog_provider = db_models.CharField(max_length=128, blank=True, default='')
    catalog_model = db_models.CharField(max_length=256, blank=True, default='')
    catalog_snapshot = db_models.CharField(max_length=64, blank=True, default='')
    gmt_create = db_models.DateTimeField(auto_now_add=True)
    gmt_deleted = db_models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'aivision_model_pricing_rule'
        indexes = [db_models.Index(fields=['space', 'provider', 'model'], name='aiv_price_rule_identity')]

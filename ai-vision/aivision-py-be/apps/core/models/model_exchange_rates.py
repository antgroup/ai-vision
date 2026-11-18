"""Immutable dated public FX snapshots, independent of canonical USD costs."""
from django.db import models


class ModelExchangeRateSnapshot(models.Model):
    snapshot_id = models.CharField(max_length=64, primary_key=True)
    rate_date = models.DateField()
    source_rates = models.JSONField(default=dict)
    rates = models.JSONField(default=dict)
    source_url = models.URLField(max_length=512)
    gmt_create = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'aivision_model_exchange_rate_snapshot'


class ModelExchangeRateState(models.Model):
    key = models.CharField(max_length=32, primary_key=True, default='ecb')
    snapshot = models.ForeignKey(ModelExchangeRateSnapshot, null=True, on_delete=models.PROTECT, db_constraint=False)
    fetched_at = models.DateTimeField(null=True)
    last_attempt_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=256, blank=True, default='')

    class Meta:
        db_table = 'aivision_model_exchange_rate_state'

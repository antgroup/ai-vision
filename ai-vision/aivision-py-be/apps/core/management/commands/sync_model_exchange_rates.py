"""Explicitly refresh public ECB reference FX for model-cost presentation."""
from django.core.management.base import BaseCommand, CommandError
from apps.core.services import model_exchange_rates as fx


class Command(BaseCommand):
    help = 'Refresh ECB reference rates for USD/CNY/JPY model cost display'

    def handle(self, *args, **options):
        try:
            snapshot = fx.sync_exchange_rates()
        except fx.ExchangeRateError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'ECB reference rates {snapshot.rate_date.isoformat()} ({snapshot.snapshot_id})')

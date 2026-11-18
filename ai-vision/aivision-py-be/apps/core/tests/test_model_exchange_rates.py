"""Dated FX evidence, exact cross rates, fail-closed refresh and Space access."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.models import User, Space, SpaceMember
from apps.core.services import model_exchange_rates as fx


def xml(day=None, usd='1.1', cny='7.7', jpy='165'):
    day = day or timezone.now().date().isoformat()
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
      xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
      <Cube><Cube time="{day}"><Cube currency="USD" rate="{usd}"/>
      <Cube currency="CNY" rate="{cny}"/><Cube currency="JPY" rate="{jpy}"/>
      </Cube></Cube></gesmes:Envelope>'''.encode()


class ExchangeRateParsingTests(SimpleTestCase):
    def test_cross_rates_use_decimal_and_original_eur_evidence(self):
        parsed = fx.parse_ecb_rates(xml(usd='1.1001'))
        self.assertEqual(parsed['sourceRates']['USD'], '1.1001')
        self.assertEqual(parsed['rates']['USD'], '1')
        self.assertEqual(parsed['rates']['CNY'], '6.999363694209617307517498')
        self.assertEqual(parsed['rateDate'], timezone.now().date())
        self.assertNotEqual(Decimal(parsed['rates']['CNY']), Decimal(7.7 / 1.1001))

    def test_rejects_missing_duplicate_nonpositive_nonfinite_or_future_data(self):
        cases = [xml().replace(b'currency="CNY"', b'currency="ABC"'),
                 xml().replace(b'</Cube></Cube>', b'<Cube currency="USD" rate="1"/></Cube></Cube>'),
                 xml(usd='0'), xml(cny='-1'), xml(jpy='NaN'), xml(jpy='Infinity'),
                 xml(day=(timezone.now().date() + timedelta(days=2)).isoformat()),
                 xml(day='wrong'), b'<!DOCTYPE x [<!ENTITY a "1">]>' + xml(),
                 '<!DOCTYPE x [<!ENTITY a "1">]>'.encode('utf-16'),
                 b'x' * (fx.MAX_RATE_BYTES + 1)]
        for body in cases:
            with self.subTest(body=body[:80]), self.assertRaises(fx.ExchangeRateError):
                fx.parse_ecb_rates(body)


class ExchangeRateServiceTests(TestCase):
    def test_empty_read_does_not_fetch_and_refresh_failure_retains_snapshot(self):
        with patch.object(fx.http_requests, 'get') as get:
            self.assertEqual(fx.exchange_rate_status()['status'], 'empty')
            get.assert_not_called()
        old = fx.store_exchange_rates(xml())
        with patch.object(fx.http_requests, 'get') as get:
            get.return_value.__enter__.return_value = get.return_value
            get.return_value.status_code = 503
            with self.assertRaises(fx.ExchangeRateError):
                fx.sync_exchange_rates()
        status = fx.exchange_rate_status()
        self.assertEqual(status['snapshotId'], old.snapshot_id)
        self.assertEqual(status['status'], 'error')
        self.assertEqual(status['rates']['CNY'], '7')
        self.assertNotIn('503', status['lastError'])

    def test_refresh_is_fixed_bounded_redirect_free_and_deduplicates_snapshots(self):
        with patch.object(fx.http_requests, 'get') as get:
            get.return_value.__enter__.return_value = get.return_value
            get.return_value.status_code = 200
            get.return_value.headers = {}
            get.return_value.iter_content.return_value = [xml()]
            first = fx.sync_exchange_rates()
            second = fx.sync_exchange_rates()
            self.assertEqual(first.snapshot_id, second.snapshot_id)
            self.assertEqual(get.call_args.args[0], fx.ECB_XML_URL)
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
            self.assertEqual(get.call_args.kwargs['timeout'], (5, 15))
        for code, headers, parts in [(302, {}, []), (200, {'Content-Length': str(fx.MAX_RATE_BYTES + 1)}, []),
                                      (200, {}, [b'x' * (fx.MAX_RATE_BYTES + 1)])]:
            with self.subTest(code=code), patch.object(fx.http_requests, 'get') as get:
                get.return_value.__enter__.return_value = get.return_value
                get.return_value.status_code = code
                get.return_value.headers = headers
                get.return_value.iter_content.return_value = parts
                with self.assertRaises(fx.ExchangeRateError):
                    fx.sync_exchange_rates()
                self.assertEqual(fx.exchange_rate_status()['snapshotId'], first.snapshot_id)

    def test_old_reference_date_is_visible_and_snapshot_is_not_backdated(self):
        fx.store_exchange_rates(xml(day=(timezone.now().date() - timedelta(days=10)).isoformat()))
        status = fx.exchange_rate_status()
        self.assertEqual(status['status'], 'stale')
        self.assertEqual(status['rateDate'], (timezone.now().date() - timedelta(days=10)).isoformat())
        self.assertIn(timezone.now().date().isoformat(), status['fetchedAt'])

    def test_late_older_response_cannot_replace_a_newer_reference_date(self):
        current = fx.store_exchange_rates(xml())
        with self.assertRaises(fx.ExchangeRateError):
            fx.store_exchange_rates(xml(day=(timezone.now().date() - timedelta(days=1)).isoformat()))
        self.assertEqual(fx.exchange_rate_status()['snapshotId'], current.snapshot_id)


class ExchangeRateApiTests(TestCase):
    def setUp(self):
        self.users = [User.objects.create(name=n, email=f'{n}@fx.test', normalized_email=f'{n}@fx.test', status=1)
                      for n in ('admin', 'member', 'outsider')]
        self.space = Space.objects.create(name='FX', type=2, creator=self.users[0], status=1)
        SpaceMember.objects.create(space=self.space, user=self.users[0], role=1)
        SpaceMember.objects.create(space=self.space, user=self.users[1], role=3)
        self.client = APIClient()
        self.base = f'/api/v1/spaces/{self.space.uuid}/model-pricing/exchange-rates'

    def test_member_reads_admin_refreshes_and_cross_space_hidden(self):
        self.client.force_authenticate(self.users[1])
        with patch.object(fx.http_requests, 'get') as get:
            response = self.client.get(self.base)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.data['data']['canManage'])
            self.assertEqual(self.client.post(self.base + '/sync', {}, format='json').status_code, 403)
            get.assert_not_called()
        self.client.force_authenticate(self.users[2])
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.client.force_authenticate(self.users[0])
        with patch.object(fx.http_requests, 'get') as get:
            get.return_value.__enter__.return_value = get.return_value
            get.return_value.status_code = 200
            get.return_value.headers = {}
            get.return_value.iter_content.return_value = [xml()]
            response = self.client.post(self.base + '/sync', {}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(response.data['data']['canManage'])
            self.assertEqual(response.data['data']['rates']['JPY'], '150')
        self.assertEqual(self.client.post(self.base + '/sync', {'url': 'https://evil.test'}, format='json').status_code, 400)

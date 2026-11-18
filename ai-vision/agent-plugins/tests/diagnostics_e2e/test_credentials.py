"""Forwarding credential renewal cache, failures, and concurrent OTLP exports."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import unittest

from credentials import RenewingIngestHeaders


class RenewingIngestHeadersTests(unittest.TestCase):
    def target(self):
        return {"space_id": "space-one", "agent_uuid": "agent-one", "agent_product": "DeepSeekHarness",
            "collector_headers": {"Authorization": "Bearer inert-initial", "X-Space": "scope-one"},
            "dashboard_headers": {"Cookie": "inert-dashboard"}}

    def test_invalid_intervals_cannot_disable_renewal_and_leave_stale_credentials_cached(self):
        for invalid in (0, -1, float("nan"), float("inf"), True, "60"):
            with self.subTest(interval=invalid), self.assertRaises(ValueError):
                RenewingIngestHeaders(self.target(), lambda target: target, refresh_interval=invalid)

    def test_initial_cache_then_exact_interval_refresh_returns_independent_header_copies(self):
        now, refreshed = [100.0], []
        original = self.target()
        saved = deepcopy(original)

        def refresh(target):
            refreshed.append(deepcopy(target))
            return {**target, "collector_headers": {**target["collector_headers"],
                "Authorization": "Bearer inert-renewed"}, "generation": 1}

        headers = RenewingIngestHeaders(original, refresh, clock=lambda: now[0])
        first = headers()
        first["Authorization"] = "inert-caller-mutation"
        original["collector_headers"]["Authorization"] = "inert-source-mutation"
        now[0] = 159.9
        self.assertEqual(headers()["Authorization"], "Bearer inert-initial")
        self.assertEqual(refreshed, [])
        now[0] = 160.0
        self.assertEqual(headers()["Authorization"], "Bearer inert-renewed")
        self.assertEqual(refreshed, [saved])
        now[0] = 219.9
        self.assertEqual(headers()["X-Space"], "scope-one")
        self.assertEqual(len(refreshed), 1)

    def test_subsequent_refresh_receives_latest_target_without_mutating_original(self):
        now, calls = [0.0], []
        original = self.target()
        saved = deepcopy(original)

        def refresh(target):
            calls.append(deepcopy(target))
            # Even a mutating adapter receives a private target copy.
            target["collector_headers"]["Authorization"] = "Bearer inert-generation-" + str(len(calls))
            target["generation"] = len(calls)
            return target

        headers = RenewingIngestHeaders(original, refresh, refresh_interval=10, clock=lambda: now[0])
        now[0] = 10
        self.assertEqual(headers()["Authorization"], "Bearer inert-generation-1")
        now[0] = 20
        self.assertEqual(headers()["Authorization"], "Bearer inert-generation-2")
        self.assertEqual(calls[1]["generation"], 1)
        self.assertEqual(original, saved)

    def test_simultaneous_expired_exports_mint_once_and_receive_the_same_new_credential(self):
        now, minted = [0.0], []
        ready = threading.Barrier(8)

        def refresh(target):
            minted.append(True)
            return {**target, "collector_headers": {"Authorization": "Bearer inert-single-renewal"}}

        headers = RenewingIngestHeaders(self.target(), refresh, clock=lambda: now[0])
        now[0] = 60

        def export():
            ready.wait(timeout=3)
            return headers()

        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda _: export(), range(8)))
        self.assertEqual(len(minted), 1)
        self.assertTrue(all(result == {"Authorization": "Bearer inert-single-renewal"} for result in results))
        results[0]["Authorization"] = "inert-caller-mutation"
        self.assertEqual(results[1]["Authorization"], "Bearer inert-single-renewal")

    def test_failed_renewal_cannot_fall_back_to_stale_credentials_and_retry_can_recover(self):
        now, attempts = [0.0], []
        original = self.target()
        saved = deepcopy(original)

        def refresh(target):
            attempts.append(deepcopy(target))
            if len(attempts) <= 2:
                target["collector_headers"]["Authorization"] = "inert-corrupted-failed-refresh"
                raise RuntimeError("inert-private-renewal-error")
            return {**target, "collector_headers": {"Authorization": "Bearer inert-recovered"}}

        headers = RenewingIngestHeaders(original, refresh, clock=lambda: now[0])
        now[0] = 901  # Beyond the production JWT's original 900-second lifetime.
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                headers()
        self.assertEqual(headers()["Authorization"], "Bearer inert-recovered")
        self.assertEqual(attempts, [saved, saved, saved])
        self.assertEqual(original, saved)
        self.assertEqual(headers()["Authorization"], "Bearer inert-recovered")
        self.assertEqual(len(attempts), 3)

    def test_refresh_interval_starts_after_successful_mint_completion(self):
        now, attempts = [0.0], []

        def refresh(target):
            attempts.append(True)
            now[0] += 30
            return {**target, "collector_headers": {"Authorization": "Bearer inert-renewed"}}

        headers = RenewingIngestHeaders(self.target(), refresh, clock=lambda: now[0])
        now[0] = 60
        headers()
        now[0] = 120
        headers()
        self.assertEqual(len(attempts), 1)
        now[0] = 150
        headers()
        self.assertEqual(len(attempts), 2)

    def test_incomplete_refresh_result_is_failure_and_does_not_poison_retry(self):
        now, attempts = [0.0], []

        def refresh(target):
            attempts.append(True)
            return {} if len(attempts) == 1 else {**target,
                "collector_headers": {"Authorization": "Bearer inert-recovered"}}

        headers = RenewingIngestHeaders(self.target(), refresh, clock=lambda: now[0])
        now[0] = 60
        with self.assertRaises((ValueError, TypeError, KeyError)):
            headers()
        self.assertEqual(headers()["Authorization"], "Bearer inert-recovered")
        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main()

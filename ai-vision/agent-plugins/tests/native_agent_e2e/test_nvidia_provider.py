import unittest
from unittest.mock import patch
import httpx
import threading
from nvidia_provider import RateBudget, StreamLedger, NvidiaProvider, json_response_facts


class NvidiaBoundaryTests(unittest.TestCase):
    def test_http_200_error_envelopes_are_not_successful_chat_or_catalog_responses(self):
        ledger=StreamLedger()
        ledger.feed(b'data: {"error":{"message":"denied"}}\n\ndata: [DONE]\n\n')
        self.assertEqual(ledger.error,'provider SSE error response')
        self.assertTrue(ledger.done)
        for method in ('GET','POST'):
            for data in ({'error':'denied'},[],None,{}):
                with self.subTest(method=method,data=data),self.assertRaises(ValueError):
                    json_response_facts(data,method)
        self.assertEqual(json_response_facts({'data':[]},'GET'),{})
        self.assertEqual(json_response_facts({'choices':[{'message':{'content':'answer'}}]},'POST')['response'],{'content':'answer'})
    def test_cancelled_budget_wait_does_not_start_another_http_attempt(self):
        budget=RateBudget(clock=lambda:0)
        for _ in range(20): budget.reserve()
        stop=threading.Event()
        stop.set()
        self.assertIsNone(budget.reserve(stop))
        self.assertEqual(budget.attempts,20)

    def test_pending_catalog_http_is_closed_and_retained_on_absolute_deadline(self):
        entered = threading.Event()
        finished = threading.Event()
        stopped = threading.Event()

        class PendingRequest:
            def __enter__(self):
                entered.set()
                try:
                    if not stopped.wait(3):
                        raise AssertionError('forwarder failed to close')
                    raise httpx.ReadTimeout('controlled pending upstream')
                finally:
                    finished.set()

            def __exit__(self,*args): pass

        class Client:
            def stream(self,*args,**kwargs): return PendingRequest()
            def close(self): stopped.set()

        # Client setup can load certificates. Finish it before starting the
        # provider clock so the deadline measures an actual pending request.
        with httpx.Client(timeout=3, trust_env=False) as caller:
            with patch('nvidia_provider.httpx.Client', return_value=Client()):
                with NvidiaProvider('private-key', RateBudget(), timeout=1) as provider:
                    try:
                        caller.get(provider.url + '/models')
                    except httpx.RemoteProtocolError:
                        pass
        self.assertTrue(entered.is_set(), 'upstream request never entered')
        self.assertTrue(stopped.is_set(), 'deadline did not close the upstream client')
        self.assertTrue(finished.is_set(), 'pending upstream request never ended')
        self.assertFalse(provider.thread.is_alive())
        self.assertFalse(provider.timer.is_alive())
        self.assertEqual(len(provider.requests), 1)
        row = provider.requests[0]
        self.assertEqual(row['method'], 'GET')
        self.assertEqual(row['error'], 'ReadTimeout')
        self.assertFalse(row.get('completed', False))
        self.assertFalse(row.get('shutdown_pending', False))
        self.assertGreaterEqual(row['http_ended_ns'], row['http_started_ns'])
        self.assertIsNone(provider.key)

    def test_non_object_sse_is_rejected_without_losing_bounded_evidence(self):
        ledger=StreamLedger()
        ledger.feed(b'data: []\n\n')
        self.assertEqual(ledger.error,'invalid provider SSE object')

    def test_account_budget_counts_all_attempts_including_auxiliary_and_failed_requests(self):
        now = [0.0]
        waits = []
        def sleep(delay):
            waits.append(delay)
            now[0] += delay
        budget = RateBudget(clock=lambda: now[0], sleep=sleep)
        for _ in range(21):
            budget.reserve()
        self.assertEqual(budget.attempts, 21)
        self.assertTrue(waits)
        self.assertGreaterEqual(now[0], 60)
        self.assertEqual(len(budget.window), 1)

    def test_stream_ledger_keeps_final_usage_without_changing_forwarded_chunks(self):
        ledger = StreamLedger()
        raw = (b'data: {"choices":[{"delta":{"content":"receipt: test"}}]}\n\n'
               b'data: {"choices":[],"usage":{"prompt_tokens":16,"completion_tokens":4,"total_tokens":20}}\n\n'
               b'data: [DONE]\n\n')
        for part in (raw[:11],raw[11:57],raw[57:]):
            ledger.feed(part)
        self.assertEqual(ledger.message()['content'], 'receipt: test')
        self.assertEqual(ledger.usage['total_tokens'], 20)
        self.assertTrue(ledger.done)

    def test_unknown_usage_stays_unknown_and_partial_tool_arguments_are_joined(self):
        ledger = StreamLedger()
        ledger.feed(b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call1","function":{"name":"read","arguments":"{\\\"path\\\":"}}]}}]}\n\n')
        ledger.feed(b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\\"receipt.txt\\\"}"}}]}}]}\n\n')
        self.assertIsNone(ledger.usage)
        self.assertEqual(ledger.message()['tool_calls'][0]['function']['arguments'], '{"path":"receipt.txt"}')

"""Explicit local NVIDIA forwarding, with one budget for every actual HTTP attempt.

Authentication stays in memory. Native payloads and streamed bytes are forwarded
unchanged; only bounded response facts enter the independent acceptance ledger.
CI never selects this optional live provider.
"""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import urlsplit
import httpx


class RateBudget:
    def __init__(self, *, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.window, self.attempts = deque(), 0
        self.lock = threading.Lock()

    def reserve(self, stop=None):
        while True:
            with self.lock:
                if stop is not None and stop.is_set():
                    return None
                now = self.clock()
                while self.window and now - self.window[0] >= 60:
                    self.window.popleft()
                if len(self.window) < 20:
                    self.window.append(now)
                    self.attempts += 1
                    return self.attempts
                wait = max(.01, 60 - (now - self.window[0]))
            if stop is None:
                self.sleep(wait)
            elif stop.wait(min(wait, 1)):
                return None


class StreamLedger:
    def __init__(self):
        self.buffer = b''
        self.content, self.tools = '', {}
        self.usage, self.done = None, False
        self.error = None

    def feed(self, data):
        self.buffer += data
        if len(self.buffer) > 2 * 1024 * 1024:
            self.error = 'response ledger limit exceeded'
            self.buffer = b''
            return
        self.buffer = self.buffer.replace(b'\r\n', b'\n')
        while b'\n\n' in self.buffer:
            event, self.buffer = self.buffer.split(b'\n\n', 1)
            payload = b'\n'.join(line[5:].lstrip() for line in event.split(b'\n') if line.startswith(b'data:'))
            if payload == b'[DONE]':
                self.done = True
                continue
            if not payload:
                continue
            try:
                value = json.loads(payload)
            except ValueError:
                self.error = 'invalid provider SSE JSON'
                continue
            if not isinstance(value, dict):
                self.error = 'invalid provider SSE object'
                continue
            if 'error' in value:
                self.error = 'provider SSE error response'
                continue
            if isinstance(value.get('usage'), dict):
                self.usage = value['usage']
            for choice in value.get('choices', []):
                delta = choice.get('delta', {})
                self.content += delta.get('content') or ''
                for call in delta.get('tool_calls', []):
                    index = call.get('index', 0)
                    target = self.tools.setdefault(index, {'id':'', 'type':'function', 'function':{'name':'', 'arguments':''}})
                    if call.get('id'):
                        target['id'] = call['id']
                    for key in ('name','arguments'):
                        target['function'][key] += call.get('function', {}).get(key) or ''
            if len(self.content) + sum(len(c['function']['arguments']) for c in self.tools.values()) > 2 * 1024 * 1024:
                self.error = 'response facts limit exceeded'
                self.content, self.tools = '', {}

    def message(self):
        result = {'role':'assistant', 'content':self.content or None}
        if self.tools:
            result['tool_calls'] = [self.tools[index] for index in sorted(self.tools)]
        return result


def json_response_facts(data, method):
    if not isinstance(data, dict) or 'error' in data:
        raise ValueError('invalid provider JSON response')
    if method == 'GET':
        if not isinstance(data.get('data'), list):
            raise ValueError('invalid provider model catalog')
        return {}
    choices = data.get('choices')
    if (not isinstance(choices, list) or not choices or not isinstance(choices[0], dict)
            or not isinstance(choices[0].get('message'), dict)):
        raise ValueError('invalid provider chat response')
    return {'response': choices[0]['message'], 'usage': data.get('usage'),
            'usage_reported': isinstance(data.get('usage'), dict)}


class NvidiaProvider:
    def __init__(self, key, budget, *, proxy=None, timeout=300):
        if proxy:
            parsed = urlsplit(proxy)
            if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.username or parsed.password:
                raise ValueError('Use an explicit credential-free loopback proxy')
        self.key, self.budget, self.proxy, self.timeout = key, budget, proxy, timeout
        self.requests, self.lock = [], threading.Lock()
        self.responses, self.workers = set(), set()
        self.stop = threading.Event()

    def stop_requests(self):
        self.stop.set()
        with self.lock:
            responses = list(self.responses)
        for response in responses:
            try:
                response.close()
            except (httpx.HTTPError, OSError):
                # Shutdown must preserve the original native/transport ledger.
                pass
        try:
            self.client.close()
        except (httpx.HTTPError, OSError):
            pass

    def __enter__(self):
        owner = self
        self.deadline = time.monotonic() + self.timeout
        self.client = httpx.Client(timeout=httpx.Timeout(self.timeout, connect=min(10,self.timeout)), proxy=self.proxy, trust_env=False,
                                   transport=None if self.proxy else httpx.HTTPTransport(retries=0))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_GET(self):
                self.forward()
            def do_POST(self):
                self.forward()
            def forward(self):
                if self.path not in ('/v1/chat/completions','/v1/models'):
                    self.send_error(404)
                    return
                length = int(self.headers.get('Content-Length','0'))
                if not 0 <= length <= 8 * 1024 * 1024:
                    self.send_error(413)
                    return
                raw = self.rfile.read(length)
                body = json.loads(raw) if raw else {}
                with owner.lock:
                    owner.workers.add(threading.current_thread())
                with owner.lock:
                    row = {'number':len(owner.requests)+1, 'request':body, 'method':self.command,
                           'usage':None, 'usage_reported':False,
                           'purpose':'agent' if body.get('tools') else 'auxiliary'}
                    owner.requests.append(row)
                ordinal = owner.budget.reserve(owner.stop)
                if ordinal is None or owner.stop.is_set() or time.monotonic() >= owner.deadline:
                    row['error'] = 'native forwarding deadline exceeded'
                    self.send_error(504)
                    with owner.lock:
                        owner.workers.discard(threading.current_thread())
                    return
                row['attempt_number'] = ordinal
                row['http_started_ns'] = time.time_ns()
                ledger = StreamLedger()
                try:
                    with owner.client.stream(self.command, 'https://integrate.api.nvidia.com' + self.path,
                        content=raw if raw else None,
                        headers={'Authorization':'Bearer ' + owner.key, 'Content-Type':'application/json'}) as response:
                        with owner.lock:
                            owner.responses.add(response)
                        row['status'] = response.status_code
                        self.send_response(response.status_code)
                        content_type = response.headers.get('Content-Type','application/json')
                        self.send_header('Content-Type',content_type)
                        self.send_header('Connection','close')
                        self.end_headers()
                        self.close_connection = True
                        chunks = []
                        for chunk in response.iter_bytes():
                            if owner.stop.is_set() or time.monotonic() >= owner.deadline:
                                row['error'] = 'native forwarding deadline exceeded'
                                break
                            if 'event-stream' in content_type:
                                ledger.feed(chunk)
                            elif sum(map(len,chunks)) + len(chunk) <= 2 * 1024 * 1024:
                                chunks.append(chunk)
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        if response.status_code != 200:
                            row['error'] = 'NVIDIA HTTP ' + str(response.status_code)
                        elif 'event-stream' in content_type:
                            row.update(response=ledger.message(), usage=ledger.usage,
                                       usage_reported=ledger.usage is not None, stream_done=ledger.done)
                            if ledger.error or not ledger.done:
                                row['error'] = ledger.error or 'incomplete provider stream'
                        else:
                            data = json.loads(b''.join(chunks))
                            row.update(json_response_facts(data, self.command))
                        row['completed'] = True
                except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
                    row['error'] = type(error).__name__
                finally:
                    row['http_ended_ns'] = time.time_ns()
                    with owner.lock:
                        if 'response' in locals():
                            owner.responses.discard(response)
                        owner.workers.discard(threading.current_thread())
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.timer = threading.Timer(self.timeout, self.stop_requests)
        self.timer.daemon = True
        self.timer.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        return self

    def __exit__(self, *_args):
        self.timer.cancel()
        self.stop_requests()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        with self.lock:
            workers = list(self.workers)
        deadline = time.monotonic() + 5
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        self.timer.join(timeout=1)
        alive = sum(worker.is_alive() for worker in workers)
        with self.lock:
            for row in self.requests:
                if alive and not row.get('completed'):
                    row['shutdown_pending'] = True
                if not row.get('completed') and not row.get('error'):
                    row['error'] = 'provider attempt did not complete before native shutdown'
        self.key = None

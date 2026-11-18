#!/usr/bin/env python3
"""Local OTLP sink used by the hard-termination Stop regression."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
import sys
import time


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        with OUTPUT.open("a", encoding="utf-8") as output:
            output.write(json.dumps(body, separators=(",", ":")) + "\n")

        request_count = len(OUTPUT.read_text(encoding="utf-8").splitlines())
        if BLOCK_AFTER and request_count > ACK_COUNT:
            BLOCKED.write_text(str(request_count), encoding="utf-8")
            while True:
                time.sleep(1)

        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        pass


if len(sys.argv) != 6:
    print(
        "usage: progressive_stop_sink.py PORT OUTPUT ACK_COUNT BLOCK_AFTER BLOCKED_MARKER",
        file=sys.stderr,
    )
    raise SystemExit(2)

PORT = int(sys.argv[1])
OUTPUT = Path(sys.argv[2])
ACK_COUNT = int(sys.argv[3])
BLOCK_AFTER = int(sys.argv[4])
BLOCKED = Path(sys.argv[5])

server = HTTPServer(("127.0.0.1", PORT), Handler)
if BLOCK_AFTER:
    server.serve_forever()
else:
    for _ in range(ACK_COUNT):
        server.handle_request()

#!/usr/bin/env python3
"""Minimal local OTLP HTTP sink for isolated cdxhooks shell regressions."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        with Path(sys.argv[2]).open("a", encoding="utf-8") as output:
            output.write("1\n")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: otel_test_sink.py PORT REQUEST_COUNT_FILE MAX_REQUESTS", file=sys.stderr)
        return 2

    server = HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    server.timeout = 15
    expected = int(sys.argv[3])
    for _ in range(expected):
        server.handle_request()

    count_path = Path(sys.argv[2])
    observed = len(count_path.read_text(encoding="utf-8").splitlines()) if count_path.exists() else 0
    return 0 if observed == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())

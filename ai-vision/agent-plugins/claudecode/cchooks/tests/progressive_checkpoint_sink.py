#!/usr/bin/env python3
"""Deterministic local OTLP sink for cchooks progressive-checkpoint tests."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", type=int)
    parser.add_argument("output", type=Path)
    parser.add_argument("ready", type=Path)
    parser.add_argument(
        "statuses",
        help="comma-separated response statuses; the last status repeats",
    )
    return parser.parse_args()


ARGS = parse_args()
STATUSES = [int(item) for item in ARGS.statuses.split(",")]
LOCK = threading.Lock()
REQUEST_COUNT = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        global REQUEST_COUNT

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        if self.path == "/metrics":
            # Keep the binary's independent health-metrics flush local and out
            # of the trace response schedule/assertions.
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            body = {"invalid_json": raw_body.decode("utf-8", errors="replace")}

        with LOCK:
            request_index = REQUEST_COUNT
            REQUEST_COUNT += 1
            status = STATUSES[min(request_index, len(STATUSES) - 1)]
            record = {
                "index": request_index + 1,
                "method": "POST",
                "path": self.path,
                "status": status,
                "body": body,
            }
            with ARGS.output.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, separators=(",", ":")) + "\n")

        self.send_response(status)
        if status == 302:
            # A redirect-following client would produce a second recorded request.
            self.send_header("Location", f"http://127.0.0.1:{ARGS.port}/redirect-target")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        global REQUEST_COUNT

        with LOCK:
            REQUEST_COUNT += 1
            record = {
                "index": REQUEST_COUNT,
                "method": "GET",
                "path": self.path,
                "status": 200,
                "body": None,
            }
            with ARGS.output.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record, separators=(",", ":")) + "\n")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        pass


ARGS.output.parent.mkdir(parents=True, exist_ok=True)
ARGS.output.write_text("", encoding="utf-8")
server = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler)
ARGS.ready.write_text("ready\n", encoding="utf-8")
server.serve_forever()

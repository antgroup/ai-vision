#!/usr/bin/env python3
"""Send one OTLP/HTTP JSON signal without installing an OpenTelemetry SDK."""

import argparse
import json
import os
import socket
import time
import uuid
from urllib.parse import urlsplit


def hex_id(byte_length):
    return uuid.uuid4().hex[: byte_length * 2]


def build_attributes(tenant_id, agent_type, platform, scene):
    attributes = []
    if tenant_id:
        attributes.append(
            {"key": "ant.tenant.id", "value": {"stringValue": tenant_id}}
        )
    if agent_type:
        attributes.append(
            {"key": "ant.agent.type", "value": {"stringValue": agent_type}}
        )
    if platform:
        attributes.append({"key": "ant.agent.platform", "value": {"stringValue": platform}})
    if scene:
        attributes.append({"key": "ant.agent.scene", "value": {"stringValue": scene}})
    return attributes


def build_resource(tenant_id, agent_type, platform, scene):
    return {"attributes": build_attributes(tenant_id, agent_type, platform, scene)}


def build_request(signal, tenant_id, agent_type, platform, scene):
    # time.time_ns() is unavailable on Python 3.6, which is still used by
    # some sandbox images. OTLP only requires an integer nanosecond timestamp.
    now = int(time.time() * 1_000_000_000)
    resource = build_resource(tenant_id, agent_type, platform, scene)

    if signal == "traces":
        return {
            "resourceSpans": [
                {
                    "resource": resource,
                    "scopeSpans": [
                        {
                            "scope": {"name": f"otlp-http-{signal}-smoke"},
                            "spans": [
                                {
                                    "traceId": hex_id(16),
                                    "spanId": hex_id(8),
                                    "name": "otlp-http-uds-smoke",
                                    "kind": 1,
                                    "startTimeUnixNano": str(now),
                                    "endTimeUnixNano": str(now + 1_000_000),
                                    "status": {"code": 1},
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    if signal == "metrics":
        return {
            "resourceMetrics": [
                {
                    "resource": resource,
                    "scopeMetrics": [
                        {
                            "scope": {"name": "otlp-http-metric-smoke"},
                            "metrics": [
                                {
                                    "name": "otlp_http_uds_smoke",
                                    "unit": "1",
                                    "gauge": {
                                        "dataPoints": [
                                            {
                                                "timeUnixNano": str(now),
                                                "asDouble": 1.0,
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    return {
        "resourceLogs": [
            {
                "resource": resource,
                "scopeLogs": [
                    {
                        "scope": {"name": "otlp-http-log-smoke"},
                        "logRecords": [
                            {
                                "timeUnixNano": str(now),
                                "severityNumber": 9,
                                "severityText": "INFO",
                                "body": {"stringValue": "otlp-http-uds-smoke"},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--signal",
        choices=("traces", "metrics", "logs"),
        default=os.getenv("OTLP_SIGNAL", "traces"),
    )
    parser.add_argument(
        "--endpoint",
        default=None,
    )
    parser.add_argument("--tenant-id", default=os.getenv("OTLP_TENANT_ID"))
    parser.add_argument(
        "--agent-type", default=os.getenv("OTLP_AGENT_TYPE", "")
    )
    parser.add_argument("--platform", default=os.getenv("OTLP_AGENT_PLATFORM", ""))
    parser.add_argument("--scene", default=os.getenv("OTLP_AGENT_SCENE", ""))
    args = parser.parse_args()

    endpoint = args.endpoint or os.getenv(
        f"OTLP_EXPORTER_OTLP_{args.signal.upper()}_ENDPOINT",
        f"http://127.0.0.1:4318/v1/{args.signal}",
    )

    body = json.dumps(
        build_request(
            args.signal, args.tenant_id, args.agent_type, args.platform, args.scene
        )
    ).encode("utf-8")
    print(f"POST {endpoint}")
    print(f"signal={args.signal}")
    print(
        f"resource_route platform={args.platform or '<from Pod metadata>'} "
        f"tenant_id={args.tenant_id or '<from Pod metadata>'} "
        f"agent_type={args.agent_type or '<from Pod metadata>'}"
    )
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or not parsed.hostname:
        print("request failed: only an HTTP URL is supported")
        return 2

    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    request = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii") + body

    try:
        with socket.create_connection((parsed.hostname, port), timeout=10) as sock:
            sock.sendall(request)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        response = b"".join(chunks).decode("utf-8", errors="replace")
        header, _, response_body = response.partition("\r\n\r\n")
        status_line = header.splitlines()[0] if header else ""
        print(status_line)
        print(response_body)
        status = int(status_line.split()[1]) if len(status_line.split()) > 1 else 0
        return 0 if 200 <= status < 300 else 1
    except OSError as error:
        print(f"request failed: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Load the product's synthetic sample and open its Trace on a local installation."""

import argparse
import getpass
import http.cookiejar
import json
import re
import sys
import time
import warnings
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)


class DemoError(RuntimeError):
    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Platform:
    """Interact only with an isolated loopback installation."""

    def __init__(self, port):
        self.origin = f"http://127.0.0.1:{port}"
        self.cookies = http.cookiejar.CookieJar()
        self.request = build_opener(
            ProxyHandler({}), NoRedirects(), HTTPCookieProcessor(self.cookies)
        )

    def api(self, path, *, body=None, params=None):
        csrf = next((cookie.value for cookie in self.cookies if cookie.name == "csrftoken"), "")
        target = self.origin + "/api/v1" + path
        if params:
            target += "?" + urlencode(params)
        request = Request(
            target,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": self.origin,
                "Referer": self.origin + "/",
                "X-CSRFToken": csrf,
            },
        )
        try:
            with self.request.open(request, timeout=60) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise DemoError(f"AI Vision returned HTTP {error.code}.", status=error.code) from None
        except (OSError, ValueError):
            raise DemoError("Local AI Vision did not return a usable response.") from None
        if not isinstance(payload, dict) or payload.get("code") != 0:
            code = payload.get("code") if isinstance(payload, dict) else None
            raise DemoError("AI Vision could not complete the operation.", code=code)
        return payload.get("data")

    def login(self, email, password):
        self.api("/auth/csrf")
        self.api("/auth/login", body={"email": email, "password": password})
        user = self.api("/auth/me")["user"]
        if user.get("email", "").casefold() != email.casefold():
            raise DemoError("The authenticated account differs from the selected account.")
        return user


def resource_id(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", value
    ):
        raise DemoError("AI Vision returned an invalid resource identity.")
    return value


def prepare_demo(client, space_id):
    endpoint = f"/spaces/{resource_id(space_id)}/onboarding/demo"
    # Provisioning and replay use the same sample identity after a transient dependency failure.
    deadline = time.monotonic() + 90
    while True:
        try:
            receipt = client.api(endpoint, body={"consent": True, "language": "en-US"})
            break
        except DemoError as error:
            if error.status != 503 or time.monotonic() + 2 >= deadline:
                raise
            time.sleep(2)
    for key in ("agent_uuid", "diagnoser_uuid"):
        resource_id(receipt[key])
    for key in ("trace_id", "comparison_trace_id"):
        value = receipt[key]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value) or not int(value, 16):
            raise DemoError("AI Vision returned an invalid sample Trace identity.")
    if receipt["comparison_trace_id"] == receipt["trace_id"]:
        raise DemoError("The sample Trace and its comparison must be distinct.")
    return receipt


def trace_complete(detail, trace_id, span_count):
    meta, spans = detail.get("meta") or {}, detail.get("spans") or []
    if (
        detail.get("traceId") != trace_id
        or meta.get("complete") is not True
        or type(span_count) is not int
        or not (span_count > 0 and meta.get("expectedSpanCount") == span_count == len(spans))
    ):
        return False
    ids = {
        span["span_id"] for span in spans
        if isinstance(span, dict) and span.get("trace_id") == trace_id
        and isinstance(span.get("span_id"), str) and re.fullmatch(r"[0-9a-f]{16}", span["span_id"])
        and int(span["span_id"], 16)
    }
    return len(ids) == span_count


def wait_for_trace(client, space_id, demo, *, comparison=False, timeout=75):
    trace_id = demo["comparison_trace_id"] if comparison else demo["trace_id"]
    span_count = demo["comparison_span_count"] if comparison else demo["span_count"]
    endpoint = f"/spaces/{space_id}/signoz/trace/{trace_id}/detail"
    params = {
        "agent_product": demo["agent_product"],
        "agent_uuid": demo["agent_uuid"],
        "detail": "1",
    }
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            detail = client.api(endpoint, params=params)
        except DemoError as error:
            if error.status not in (404, 429) and error.code not in (3019, 3035):
                raise
        else:
            if detail and trace_complete(detail, trace_id, span_count):
                return
        time.sleep(1)
    raise DemoError("The sample Trace did not become readable before the timeout.")


def port_number(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Use a local TCP port between 1 and 65535.")
    return port


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-port", type=port_number, default=5174)
    parser.add_argument(
        "--otlp-port", type=port_number, help="Compatibility option for existing installers."
    )
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.error("Run this command in an interactive terminal to enter your local password.")
    try:
        print("This guide imports the built-in synthetic sample into your personal space.")
        if input("Import the example [y/N]? ").strip().lower() != "y":
            return 0
        email = input("AI Vision email: ").strip()
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("AI Vision password: ")
        client = Platform(args.web_port)
        client.login(email, password)
        spaces = client.api("/spaces")["items"]
        personal = [row for row in spaces if row.get("type") == 1 and row.get("is_owner") is True]
        if len(personal) != 1:
            raise DemoError("The authenticated user does not have one personal space.")
        space_id = resource_id(personal[0]["space_id"])
        demo = prepare_demo(client, space_id)
        wait_for_trace(client, space_id, demo)
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        parser.exit(1, "Sample Trace cancelled.\n")
    except DemoError as error:
        parser.exit(1, f"Sample Trace: {error}\n")
    except (AttributeError, KeyError, TypeError, ValueError):
        parser.exit(1, "Sample Trace: AI Vision returned an unsupported response.\n")
    query = urlencode({
        "spaceId": space_id, "agent": demo["agent_product"], "agent_uuid": demo["agent_uuid"],
        "traceId": demo["trace_id"], "source_system": "signoz",
    })
    print(f"Open the synthetic sample in your signed-in browser:\n{client.origin}/trace?{query}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

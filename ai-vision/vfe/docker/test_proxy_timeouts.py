#!/usr/bin/env python3
"""Exercise the current proxy template in nginx with a cached frontend image.

Usage: python3 docker/test_proxy_timeouts.py IMAGE
Only isolated fixture containers are created; no image is built or downloaded.
The diagnostic response header exposes the selected location's configured budget
without making the regression suite wait ten minutes for each request.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import uuid

from smoke import docker, request


def check_template(image):
    template = Path(__file__).with_name("nginx.conf.template").read_text()
    instrumented = re.sub(
        r"(proxy_read_timeout\s+(\d+)s;)",
        r'\1\n        add_header X-Test-Proxy-Budget "\2" always;',
        template,
    )
    prefix = "aiv-trial-proxy-" + uuid.uuid4().hex[:10]
    network, upstream = prefix + "-network", prefix + "-upstream"
    names = []
    with tempfile.TemporaryDirectory(prefix="aiv-trial-proxy-") as directory:
        directory = Path(directory)
        proxy = directory / "proxy.template"
        proxy.write_text(instrumented)
        fixture = directory / "upstream.conf"
        fixture.write_text('''worker_processes 1;
events { worker_connections 64; }
http { server { listen 8080;
    location / {
        default_type application/json;
        return 200 '{"uri":"$request_uri","proto":"$http_x_forwarded_proto","method":"$request_method"}';
    }
} }
''')
        try:
            docker("network", "create", network)
            names.append(upstream)
            docker(
                "run", "-d", "--pull", "never", "--name", upstream,
                "--network", network, "--network-alias", "upstream",
                "--entrypoint", "nginx", "--mount",
                f"type=bind,source={fixture},target=/etc/nginx/nginx.conf,readonly",
                image, "-g", "daemon off;",
            )
            for trusted in ("false", "true"):
                name = prefix + "-" + trusted
                names.append(name)
                docker(
                    "run", "-d", "--pull", "never", "--name", name,
                    "--network", network, "-p", "127.0.0.1::18174",
                    "-e", "FE_PORT=18174", "-e", "BE_UPSTREAM=http://upstream:8080",
                    "-e", f"AIVISION_TRUST_PROXY_HEADERS={trusted}", "--mount",
                    f"type=bind,source={proxy},target=/etc/nginx/templates/default.conf.template,readonly",
                    image,
                )
                port = docker("port", name, "18174/tcp", text=True).strip().rsplit(":", 1)[1]
                deadline = time.monotonic() + 20
                while True:
                    try:
                        request(port, "/api/fixture")
                        break
                    except (OSError, urllib.error.URLError):
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.1)
                docker("exec", name, "nginx", "-t", stderr=subprocess.STDOUT)
                api_proto = "https" if trusted == "true" else "http"
                # Literal public routes and near misses verify nginx's actual
                # location selection, including query strings and URI anchoring.
                cases = (
                    ("/api/v1/spaces/example/diagnose/diagnosers/rule/trial", 630, api_proto),
                    ("/api/v1/spaces/example/diagnose/diagnosers/rule/trial?from=editor", 630, api_proto),
                    ("/api/v1/spaces/example/diagnose/diagnosers/llm/trial", 963, "http"),
                    ("/api/v1/spaces/example/diagnose/specialists/trial", 963, "http"),
                    ("/api/v1/spaces/example/diagnose/diagnosers/rule/trial/history", 300, api_proto),
                    ("/api/v1/spaces/example/diagnose/diagnosers/rule/trial-extra", 300, api_proto),
                    ("/api/v1/spaces/example/diagnose/diagnosers/rule/trials", 300, api_proto),
                    ("/api/v1/spaces/example/diagnose/diagnosers/llm/trial/extra", 300, api_proto),
                    ("/api/v1/spaces/example/diagnose/specialists/trial/extra", 300, api_proto),
                    ("/api/v1/spaces/example/integrations/sandbox-connections", 300, api_proto),
                    ("/api/v1/ordinary", 300, api_proto),
                    ("/openapi/ordinary", 300, api_proto),
                )
                for path, seconds, proto in cases:
                    body, headers = request(port, path, method="POST")
                    actual = headers.get("X-Test-Proxy-Budget")
                    assert actual == str(seconds), (path, actual, seconds)
                    assert json.loads(body) == {"uri": path, "proto": proto, "method": "POST"}
            print("PASS current nginx template: Rule 630s, LLM 963s, ordinary 300s; exact routes and proxy trust")
        finally:
            for name in reversed(names):
                subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)
            subprocess.run(["docker", "network", "rm", network], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    check_template(parser.parse_args().image)

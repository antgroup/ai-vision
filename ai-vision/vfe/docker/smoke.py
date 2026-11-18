#!/usr/bin/env python3
"""Check a built frontend image without contacting a real backend.

Usage: python3 docker/smoke.py IMAGE [--baseline BASELINE_IMAGE]
Requires Docker and Python 3; uses an isolated network and temporary containers.
"""

import argparse
import hashlib
import http.client
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid


def docker(*args, **kwargs):
    return subprocess.check_output(["docker", *args], **kwargs)


def inspect(image):
    return json.loads(docker("image", "inspect", image))[0]


def static_files(image):
    data = docker("run", "--rm", "--network", "none", "--read-only",
                  "--entrypoint", "tar", image,
                  "-C", "/usr/share/nginx/html", "-cf", "-", ".")
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return {member.name: hashlib.sha256(archive.extractfile(member).read()).hexdigest()
                for member in archive if member.isfile()}


def request(port, path, *, method="GET", data=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        headers={"Host": "smoke.example", "X-Forwarded-Proto": "https",
                 "Authorization": "Bearer smoke-token", "Cookie": "session=smoke"},
        data=(b"{}" if data is None else data) if method == "POST" else None,
    )
    # Host-local checks must not inherit a workstation's HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as response:
        return response.read(), response.headers


def check_trace_ingest_budget(port):
    trace_path = "/api/public/otel/v1/traces"
    # Two Base64-encoded 16 MiB attachments need more than 32 MiB on the wire.
    fixture_size = 2 * (4 * (((16 << 20) + 2) // 3))
    body, _ = request(port, trace_path, method="POST", data=b"x" * fixture_size)
    assert json.loads(body)["uri"] == trace_path, "OTLP-sized body did not reach upstream"
    for path, size in ((trace_path, (64 << 20) + 1), ("/api/v1/smoke", fixture_size)):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            # For sizes over the limit nginx must reject on the header alone.
            conn.request("POST", path, headers={"Content-Length": str(size)})
            with conn.getresponse() as response:
                assert response.status == 413, (path, size, response.status)
        finally:
            conn.close()


def check_image(image, network, prefix):
    config = inspect(image)["Config"]
    assert config["Entrypoint"] == ["/docker-entrypoint.sh"], config
    assert config["Cmd"] == ["nginx", "-g", "daemon off;"], config
    assert config["StopSignal"] == "SIGQUIT", config
    docker("run", "--rm", "--network", "none", "--read-only", "--entrypoint", "sh", image,
           "-ec", "command -v curl; command -v envsubst; "
           "test -s /etc/ssl/certs/ca-certificates.crt; "
           "command -v update-ca-certificates; "
           "test \"$(TZ=Asia/Shanghai date +%z)\" = +0800")
    for trusted in ("false", "true"):
        name = f"{prefix}-fe-{trusted}"
        try:
            docker("run", "-d", "--name", name, "--network", network, "--cpus", "1",
                   "-p", "127.0.0.1::18174", "-e", "FE_PORT=18174",
                   "-e", "NGINX_ENTRYPOINT_WORKER_PROCESSES_AUTOTUNE=1",
                   "-e", "BE_UPSTREAM=http://upstream:8080",
                   "-e", f"AIVISION_TRUST_PROXY_HEADERS={trusted}", image)
            port = docker("port", name, "18174/tcp", text=True).strip().rsplit(":", 1)[1]
            deadline = time.monotonic() + 20
            while True:
                try:
                    index, _ = request(port, "/")
                    break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
            assert b'id="root"' in index and b"<title>AI Vision</title>" in index, "Expected the production SPA"
            assert request(port, "/spaces/example/dashboard")[0] == index, "SPA fallback failed"
            assets = re.findall(rb'(?:src|href)="(/assets/[^\"]+)"', index)
            assert any(asset.endswith(b".js") for asset in assets), "No built JavaScript found"
            for asset in assets:
                content, headers = request(port, asset.decode())
                assert content and "immutable" in ",".join(headers.get_all("Cache-Control", [])), headers
            if trusted == "false":
                worker = docker("exec", name, "sh", "-ec",
                                'printf "%s\\n" /usr/share/nginx/html/assets/pdf.worker*.mjs',
                                text=True).strip()
                assert "*" not in worker, "PDF module worker is missing from the image"
                content, headers = request(port, "/assets/" + Path(worker).name)
                assert content and headers.get_content_type() in ("application/javascript", "text/javascript"), headers
                assert "immutable" in ",".join(headers.get_all("Cache-Control", [])), headers
                check_trace_ingest_budget(port)
            try:
                request(port, "/assets/does-not-exist.js")
                raise AssertionError("Missing asset must return HTTP 404")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404, exc.code
            expected_proto = "https" if trusted == "true" else "http"
            paths = (
                ("/api/v1/smoke?value=1", expected_proto),
                ("/openapi/smoke", expected_proto),
                ("/project/example/traces", "http"),
                ("/api/v1/spaces/example/diagnose/diagnosers/llm/trial", "http"),
                ("/api/v1/spaces/example/diagnose/specialists/trial", "http"),
                ("/api/v1/spaces/example/diagnose/diagnosers/rule/trial", expected_proto),
            )
            for path, proto in paths:
                payload = json.loads(request(port, path, method="POST")[0])
                assert payload == {
                    "uri": path, "host": "smoke.example", "proto": proto,
                    "method": "POST", "authorization": "Bearer smoke-token",
                    "cookie": "session=smoke",
                }, payload
            docker("exec", name, "nginx", "-t", stderr=subprocess.STDOUT)
            # Match the shell/curl contract of generated Compose health checks.
            docker("exec", name, "sh", "-ec", "curl -fsS http://127.0.0.1:18174/ >/dev/null")
            docker("stop", "-t", "5", name)
            state = json.loads(docker("inspect", name))[0]["State"]
            assert state["ExitCode"] == 0 and not state["OOMKilled"], state
        except BaseException:
            subprocess.run(["docker", "logs", "--tail", "80", name], check=False)
            raise
        finally:
            subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)
    print(f"PASS {image}: startup/envsubst, nginx config, SPA/assets, proxy routes, health and shutdown")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--baseline")
    args = parser.parse_args()
    if args.baseline:
        assert static_files(args.image) == static_files(args.baseline), "Static bundle changed"
        for path in ("/usr/sbin/nginx", "/etc/nginx/templates/default.conf.template"):
            hashes = [docker("run", "--rm", "--network", "none", "--read-only",
                             "--entrypoint", "sha256sum", image, path).split()[0]
                      for image in (args.image, args.baseline)]
            assert hashes[0] == hashes[1], f"Changed runtime contract: {path}"
        print("PASS baseline comparison: identical nginx executable, site template and static bundle")
    prefix = f"aiv-slim-fe-{uuid.uuid4().hex[:10]}"
    network, upstream = f"{prefix}-network", f"{prefix}-upstream"
    with tempfile.TemporaryDirectory(prefix="aiv-slim-fe-") as directory:
        config = Path(directory) / "upstream.conf"
        config.write_text('''worker_processes 1;
events { worker_connections 64; }
http {
server {
    listen 8080;
    client_max_body_size 64m;
    location / {
        default_type application/json;
        return 200 '{"uri":"$request_uri","host":"$http_host","proto":"$http_x_forwarded_proto","method":"$request_method","authorization":"$http_authorization","cookie":"$http_cookie"}';
    }
}
}
''')
        try:
            # A dedicated bridge keeps the stub separate from existing services;
            # published checks bind only to loopback. Internal bridges suppress
            # published ports on some Docker versions.
            docker("network", "create", network)
            docker("run", "-d", "--name", upstream, "--network", network,
                   "--network-alias", "upstream", "--entrypoint", "nginx",
                   "--mount", f"type=bind,source={config},target=/etc/nginx/nginx.conf,readonly",
                   args.image, "-g", "daemon off;")
            for image in dict.fromkeys(filter(None, (args.baseline, args.image))):
                check_image(image, network, prefix)
        finally:
            subprocess.run(["docker", "rm", "-f", upstream], check=False, stdout=subprocess.DEVNULL)
            subprocess.run(["docker", "network", "rm", network], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    main()

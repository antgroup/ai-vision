#!/usr/bin/env python3
"""Accept a complete Compose deployment, including persistence and image updates.

Requires a supplied aiv CLI and Docker Compose v2. Python 3.10+ runs this
acceptance harness only; deployment control uses the Go CLI and native Compose.
Use --backend oceanbase|seekdb, optionally --external. --images-json and
--upgrade-images-json accept JSON objects or paths to JSON files, mapping
signoz/gateway/backend/agentlens/frontend to image references. At least one
running application image ID must change during upgrade.

Only unique resources created by this run are removed. Raw state and credentials
remain in a private temporary directory; --out receives sanitized evidence.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from seekdb_deploy_smoke import (
    CONTAINER_VARS, IMAGE_VARS, SECRET_VARS, SEEKDB_IMAGE, Smoke, TraceCanary, ports,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_ALIASES = {
    key
    for component in json.loads((ROOT / "deploy/components.json").read_text())["components"]
    for role, legacy in component["configAliases"].items()
    for key in (role, legacy)
}
SERVICES = ("database", "data-backend", "ingest-gateway", "backend", "diagnosis", "frontend")
ROLE_VARS = dict(zip(SERVICES, CONTAINER_VARS))
IMAGE_SERVICES = dict(zip(IMAGE_VARS, SERVICES[1:]))
FIXTURE_READY = """import json, sys, time, pymysql
options = json.load(sys.stdin)
for attempt in range(200):
    try:
        with pymysql.connect(host=options['host'], port=2881, user=options['user'],
                             password=options['password'], connect_timeout=5,
                             read_timeout=5, write_timeout=5) as db:
            with db.cursor() as cursor:
                cursor.execute('SELECT 1')
        break
    except pymysql.err.OperationalError as error:
        if attempt == 199:
            raise RuntimeError('external fixture database did not become ready') from None
        time.sleep(3)
print('external fixture ready')
"""


def image_overrides(value):
    try:
        data = json.loads(value) if value.lstrip().startswith("{") else json.loads(Path(value).read_text())
    except (OSError, ValueError) as error:
        raise argparse.ArgumentTypeError("expected an image JSON object or readable JSON file") from error
    if not isinstance(data, dict) or set(data) - IMAGE_VARS.keys() or any(
        not isinstance(image, str) or not image.strip() for image in data.values()
    ):
        raise argparse.ArgumentTypeError("images must map signoz/gateway/backend/agentlens/frontend to references")
    return data


def write_env(path, values):
    # JSON quotes preserve punctuation in Compose dotenv values. All generated
    # credentials are URL-safe and contain no Compose interpolation expressions.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        for key, value in sorted(values.items()):
            stream.write(f"{key}={json.dumps(str(value))}\n")


def organization_output(output):
    # Django startup logging can write to stderr, which Smoke.run joins into
    # its evidence stream. The management command's final stdout line is the ID.
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("backend returned no org identity")
    try:
        uuid.UUID(lines[-1])
    except ValueError:
        raise AssertionError("backend returned an invalid org identity") from None
    return lines[-1]


def snapshot_assets(runtime, compose_name, source=ROOT / "deploy"):
    """Keep a run independent of concurrent generator or bootstrap edits."""
    destination = runtime / "deploy"
    (destination / "docker").mkdir(parents=True)
    shutil.copy2(source / "docker" / compose_name, destination / "docker" / compose_name)
    shutil.copytree(source / "bootstrap", destination / "bootstrap",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return destination / "docker" / compose_name


class ComposeSmoke(Smoke):
    def __init__(self, out, runtime, values, compose_file, env_file, external, cli=Path("aiv")):
        super().__init__(out, runtime, values)
        # Host deployment variables otherwise override the private dotenv file.
        for key in list(self.env):
            if key in values or key in CONFIG_ALIASES or key.startswith("COMPOSE_"):
                self.env.pop(key)
        self.compose_file, self.env_file, self.external = compose_file, env_file, external
        self.command = ["docker", "compose", "--file", str(compose_file), "--env-file", str(env_file)]
        self.cli = cli
        self.services = SERVICES[1:] if external else SERVICES
        self.containers = {}
        self.stop_events = []
        self.fixture_containers, self.fixture_volumes, self.fixture_networks = [], [], []
        self.owned_volumes = []
        self.volume_data = None

    def lifecycle_command(self, action, *arguments):
        command = {"up": "cast", "down": "destroy"}.get(action, action)
        return [str(self.cli), command, "--target", "compose", "--compose-file", str(self.compose_file),
                "--compose-env-file", str(self.env_file), *(["-y"] if action == "down" else []), *arguments]

    def create_volume(self, name, labels=None):
        existing = self.run("volume-check-absent-" + name, [
            "docker", "volume", "inspect", "--format", "{{.Name}}", name], check=False)
        if existing.returncode == 0:
            raise RuntimeError("refusing to reuse existing volume: " + name)
        labels = {
            "io.aivision.product": "aivision",
            "io.aivision.namespace": self.values["NAMESPACE"],
            "io.aivision.state-dir": str(Path(self.values["AIV_STATE_DIR"]).resolve()),
            "com.docker.compose.project": self.values["NAMESPACE"],
            "com.docker.compose.volume": name,
            **(labels or {}),
        }
        labels["io.aivision.test-run"] = self.values["NAMESPACE"]
        args = ["docker", "volume", "create"]
        for key, value in sorted(labels.items()):
            args.extend(["--label", key + "=" + value])
        if self.volume_data is not None:
            directory = self.volume_data / hashlib.sha256(name.encode()).hexdigest()[:16]
            directory.mkdir()
            args.extend(["--driver", "local", "--opt", "type=none",
                         "--opt", "o=bind", "--opt", "device=" + str(directory)])
        self.owned_volumes.append(name)
        self.run("volume-create-" + name, [*args, name])

    def prepare_volumes(self, root):
        self.volume_data = Path(tempfile.mkdtemp(
            prefix=self.values["NAMESPACE"] + "-volumes-", dir=root)).resolve()
        config = json.loads(self.run("volume-configuration", [
            *self.command, "--profile", "bootstrap", "config", "--format", "json"]).stdout)
        for key, volume in config.get("volumes", {}).items():
            if volume.get("external"):
                raise RuntimeError("acceptance must own every application volume")
            labels = dict(volume.get("labels", {}))
            labels.update({"com.docker.compose.project": config["name"],
                           "com.docker.compose.volume": key})
            self.create_volume(volume["name"], labels)

    def discover(self, label):
        for service in self.services:
            output = self.run(f"{label}-id-{service}", [*self.command, "ps", "-aq", service]).stdout
            ids = [line for line in output.splitlines() if re.fullmatch(r"[a-f0-9]{64}", line)]
            if len(ids) != 1:
                raise AssertionError(f"expected one Compose container for {service}")
            self.containers[service] = ids[0]

    def healthy(self, label):
        self.discover(label)
        for service, container in self.containers.items():
            state = json.loads(self.run(f"{label}-state-{service}", [
                "docker", "inspect", "--format", "{{json .State}}", container]).stdout)
            if not state["Running"] or state["OOMKilled"]:
                raise AssertionError(f"{service} stopped or was OOM killed")
            if state.get("Health", {}).get("Status", "healthy") != "healthy":
                raise AssertionError(f"{service} did not become healthy")
        url = "http://127.0.0.1:" + self.values["FE_PORT"] + "/"
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=15) as response:
            if response.status != 200:
                raise AssertionError("frontend did not return HTTP 200")
        return {"services": list(self.services), "web_http_status": 200, "oom_killed": False}

    def identity(self, label):
        org = organization_output(self.run(f"{label}-org", ["docker", "exec", self.containers["backend"],
                                  "/bin/sh", "-ec", 'if [ -x /app/manage ]; then exec /app/manage "$@"; fi; exec python manage.py "$@"', "aiv-manage", "signoz_integration_status",
                                  "--print-org", "--require"]).stdout)
        observed = self.run(f"{label}-diagnosis-org", ["docker", "exec", self.containers["diagnosis"],
                            "python", "-c",
                            "from pathlib import Path; "
                            "values=dict(item.split(b'=',1) for item in Path('/proc/1/environ').read_bytes().split(b'\\0') if b'=' in item); "
                            "print(values.get(b'SIGNOZ_ORG_ID', b'').decode())"]).stdout.strip()
        if observed != org:
            raise AssertionError("AgentLens did not receive the bootstrapped org identity")
        volumes = {}
        for service, container in self.containers.items():
            mounts = json.loads(self.run(f"{label}-mounts-{service}", [
                "docker", "inspect", "--format", "{{json .Mounts}}", container]).stdout)
            volumes[service] = sorted((mount["Destination"], mount.get("Name") or mount["Source"])
                                      for mount in mounts)
        for name in self.fixture_containers[-1:]:
            mounts = json.loads(self.run(f"{label}-fixture-mounts", [
                "docker", "inspect", "--format", "{{json .Mounts}}", name]).stdout)
            volumes["external-database"] = sorted((mount["Destination"], mount.get("Name") or mount["Source"])
                                                  for mount in mounts)
        return {"organization_id": org, "persistent_mounts": volumes}

    def images(self, label):
        return {service: self.run(f"{label}-image-{service}", [
            "docker", "inspect", "--format", "{{.Image}}", self.containers[service]]).stdout.strip()
            for service in IMAGE_SERVICES.values()}

    def resource_snapshot(self, label):
        result = {}
        for service, container in self.containers.items():
            stats = self.run(f"{label}-stats-{service}", [
                "docker", "stats", "--no-stream", "--format", "{{json .}}", container]).stdout
            state = json.loads(self.run(f"{label}-resource-state-{service}", [
                "docker", "inspect", "--format",
                '{"pid":{{.State.Pid}},"restarts":{{.RestartCount}},"oom":{{.State.OOMKilled}},'
                '"memory_limit":{{.HostConfig.Memory}},"nano_cpus":{{.HostConfig.NanoCpus}}}', container]).stdout)
            entry = dict(state, stats=json.loads(stats), cgroup={})
            # Optional local-daemon measurements; do not execute shell tools in
            # product images or assume a remote daemon shares the host /proc.
            try:
                for line in Path(f"/proc/{state['pid']}/cgroup").read_text().splitlines():
                    _, controllers, location = line.split(":", 2)
                    if controllers == "":
                        root = Path("/sys/fs/cgroup") / location.lstrip("/")
                        for name in ("cpu.stat", "memory.peak", "memory.events"):
                            path = root / name
                            if path.exists():
                                entry["cgroup"][name] = path.read_text().strip()
                    elif "cpu" in controllers.split(","):
                        path = Path("/sys/fs/cgroup/cpu") / location.lstrip("/") / "cpu.stat"
                        if path.exists():
                            entry["cgroup"]["cpu.stat"] = path.read_text().strip()
                    elif "memory" in controllers.split(","):
                        root = Path("/sys/fs/cgroup/memory") / location.lstrip("/")
                        for name in ("memory.max_usage_in_bytes", "memory.failcnt"):
                            path = root / name
                            if path.exists():
                                entry["cgroup"][name] = path.read_text().strip()
            except OSError:
                entry["cgroup_unavailable"] = True
            entry.pop("pid")
            result[service] = entry
            if state["restarts"] or state["oom"]:
                raise AssertionError(f"{service} restarted or was OOM killed during {label}")
        return result

    def stop_services(self, label, services=None):
        # Reverse dependency order prevents a healthy consumer from losing its
        # datastore while it drains. Compose uses each configured grace period.
        for service in services or reversed(self.services):
            started = time.monotonic()
            self.run(f"{label}-stop-{service}", [*self.command, "stop", service], timeout=420)
            state = json.loads(self.run(f"{label}-stopped-{service}", [
                "docker", "inspect", "--format", "{{json .State}}", self.containers[service]]).stdout)
            event = {"phase": label, "service": service, "seconds": round(time.monotonic() - started, 3),
                     "exit_code": state["ExitCode"], "oom_killed": state["OOMKilled"]}
            logs = self.run(f"{label}-stop-logs-{service}", [
                "docker", "logs", "--tail", "100", self.containers[service]], check=False).stdout
            seekdb_stop = re.findall(r"seekdb stop: child_exit=(\d+) forced=(true|false)", logs)
            if seekdb_stop:
                child_exit, forced = seekdb_stop[-1]
                event["database_child_exit"] = int(child_exit)
                event["database_forced"] = forced == "true"
            self.stop_events.append(event)
            if state["Running"] or state["OOMKilled"] or state["ExitCode"] != 0:
                raise AssertionError(f"{service} failed the bounded-stop check: exit {state['ExitCode']}")

    def recover_dependency(self, trace, namespace):
        self.stop_services("dependency-failure", ["data-backend"])
        state = json.loads(self.run("gateway-during-dependency-failure", [
            "docker", "inspect", "--format", "{{json .State}}", self.containers["ingest-gateway"]]).stdout)
        if not state["Running"] or state["OOMKilled"]:
            raise AssertionError("gateway exited when data-backend stopped")
        self.run("dependency-start", [*self.command, "start", "data-backend"])
        url = "http://127.0.0.1:" + self.values["SIGNOZ_PORT"] + "/api/v1/ready"
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 180
        while True:
            try:
                with client.open(url, timeout=3) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                pass
            if time.monotonic() >= deadline:
                raise AssertionError("data-backend did not recover after restart")
            time.sleep(2)
        recovered = TraceCanary(self, namespace + "-recovered")
        recovered.ingest()
        return {"gateway_alive_while_dependency_stopped": True,
                "new_trace": recovered.read(), "retained_trace": trace.read()}

    def start_external(self):
        values, namespace = self.values, self.values["NAMESPACE"]
        network = values["DOCKER_NETWORK"]
        name, volume = namespace + "-external-database", namespace + "-external-data"
        resources = [("network", network), ("container", name), ("volume", volume)]
        if values["OB_BACKEND"] == "oceanbase":
            resources.append(("volume", namespace + "-external-config"))
        else:
            resources.extend([("network", namespace + "-external-init-net"),
                              ("container", name + "-initialize")])
        for kind, resource in resources:
            existing = self.run("fixture-check-absent-" + resource, [
                "docker", kind, "inspect", "--format", "{{.Name}}", resource], check=False)
            if existing.returncode == 0:
                raise RuntimeError(f"refusing to reuse existing {kind}: {resource}")
        self.fixture_networks.append(network)
        self.run("fixture-network", ["docker", "network", "create",
                 "--label", "com.docker.compose.project=" + namespace,
                 "--label", "com.docker.compose.network=stack", network])
        self.fixture_containers.append(name)
        self.fixture_volumes.append(volume)
        self.create_volume(volume)
        values["OB_HOST"] = name
        common = ["docker", "run", "--name", name, "--network", network]
        if values["OB_BACKEND"] == "oceanbase":
            config = namespace + "-external-config"
            self.fixture_volumes.append(config)
            self.create_volume(config)
            self.env.update({key: values[key] for key in (
                "OB_TENANT_PASSWORD", "OB_SYS_PASSWORD", "OB_TENANT_NAME",
                "OB_MEMORY_LIMIT", "OB_DATAFILE_SIZE", "OB_LOG_DISK_SIZE")})
            args = ["-e", "MODE=mini"]
            for key in ("OB_TENANT_PASSWORD", "OB_SYS_PASSWORD", "OB_TENANT_NAME",
                        "OB_MEMORY_LIMIT", "OB_DATAFILE_SIZE", "OB_LOG_DISK_SIZE"):
                args.extend(["-e", key])
            self.run("fixture-start", [*common, "-d", "-v", volume + ":/root/ob",
                     "-v", config + ":/root/.obd/cluster",
                     "--mount", f"type=bind,source={self.compose_file.parent.parent / 'bootstrap/oceanbase.sh'},target=/aiv-oceanbase.sh,readonly",
                     "--entrypoint", "/bin/sh", *args, values["OB_IMAGE"], "/aiv-oceanbase.sh"])
        else:
            init_network, init_name = namespace + "-external-init-net", name + "-initialize"
            self.fixture_networks.append(init_network)
            self.fixture_containers.insert(0, init_name)
            self.run("fixture-init-network", ["docker", "network", "create", "--internal", init_network])
            self.env["AIV_SEEKDB_PASSWORD"] = values["OB_TENANT_PASSWORD"]
            shared = ["--ulimit", "nofile=65536:65536", "-v", volume + ":/var/lib/seekdb",
                      "--mount", f"type=bind,source={self.compose_file.parent.parent / 'bootstrap/seekdb.sh'},target=/aiv-seekdb.sh,readonly",
                      "-e", "AIV_SEEKDB_PASSWORD", "--entrypoint", "/bin/sh", values["OB_IMAGE"], "/aiv-seekdb.sh"]
            self.run("fixture-initialize", ["docker", "run", "--name", init_name,
                     "--network", init_network, *shared, "initialize"], timeout=1500)
            self.run("fixture-start", [*common, "-d", *shared, "serve"])
        self.run("fixture-ready", ["docker", "run", "--rm", "-i", "--network", network,
                 "--entrypoint", "python", values["AGENTLENS_IMAGE"], "-c", FIXTURE_READY],
                 stdin=json.dumps({"host": name, "user": values["OB_USER"],
                                   "password": values["OB_TENANT_PASSWORD"]}), timeout=1200)

    def cleanup(self):
        errors = []
        try:
            result = self.run("purge", self.lifecycle_command("down", "--purge"), timeout=300, check=False)
            if result.returncode:
                errors.append("Compose purge failed")
        except Exception as error:
            errors.append(self.sanitize(error))
        for name in self.fixture_containers[-1:]:
            try:
                started = time.monotonic()
                self.run("fixture-stop", ["docker", "stop", "--time", "120", name], timeout=150, check=False)
                result = self.run("fixture-stopped", [
                    "docker", "inspect", "--format", "{{json .State}}", name], check=False)
                if result.returncode == 0:
                    state = json.loads(result.stdout)
                    event = {"phase": "cleanup", "service": "external-database",
                             "seconds": round(time.monotonic() - started, 3),
                             "exit_code": state["ExitCode"], "oom_killed": state["OOMKilled"]}
                    logs = self.run("fixture-stop-logs", [
                        "docker", "logs", "--tail", "100", name], check=False).stdout
                    seekdb_stop = re.findall(r"seekdb stop: child_exit=(\d+) forced=(true|false)", logs)
                    if seekdb_stop:
                        child_exit, forced = seekdb_stop[-1]
                        event["database_child_exit"] = int(child_exit)
                        event["database_forced"] = forced == "true"
                    self.stop_events.append(event)
                    if state["Running"] or state["OOMKilled"] or state["ExitCode"] != 0:
                        errors.append("external database failed the bounded-stop check")
            except Exception as error:
                errors.append(self.sanitize(error))
        volumes = list(dict.fromkeys([*self.fixture_volumes, *self.owned_volumes]))
        remaining_volumes = False
        for kind, names in (("container", self.fixture_containers), ("volume", volumes),
                            ("network", self.fixture_networks)):
            for name in names:
                remove = ["docker", "rm", "-f", name] if kind == "container" else ["docker", kind, "rm", name]
                try:
                    if kind == "volume":
                        owner = self.run("volume-cleanup-owner-" + name, [
                            "docker", "volume", "inspect", "--format", "{{json .Labels}}", name], check=False)
                        if owner.returncode == 0 and json.loads(owner.stdout or "{}").get(
                                "io.aivision.test-run") != self.values["NAMESPACE"]:
                            raise RuntimeError("refusing to remove a volume not owned by this test: " + name)
                    self.run("cleanup-" + name, remove, check=False, timeout=180)
                    remaining = self.run("remaining-" + name, [
                        "docker", kind, "inspect", "--format", "{{.Name}}", name], check=False)
                    if remaining.returncode == 0:
                        errors.append(f"{kind} remains: {name}")
                        remaining_volumes |= kind == "volume"
                except Exception as error:
                    errors.append(self.sanitize(error))
                    remaining_volumes |= kind == "volume"
        if self.volume_data is not None and not remaining_volumes:
            try:
                # Database-created directories can be owned by container root.
                # Only this invocation's private volume tree is mounted here.
                self.run("cleanup-volume-data", ["docker", "run", "--rm", "--network", "none",
                    "--mount", f"type=bind,source={self.volume_data},target=/cleanup",
                    "--entrypoint", "python", self.values["AGENTLENS_IMAGE"], "-c",
                    "from pathlib import Path; import shutil; "
                    "[(shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()) "
                    "for p in Path('/cleanup').iterdir()]"])
                self.volume_data.rmdir()
            except Exception as error:
                errors.append(self.sanitize(error))
        return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", choices=("oceanbase", "seekdb"), default="oceanbase")
    parser.add_argument("--aiv", type=Path, required=True, help="candidate Go CLI with explicit Compose support")
    parser.add_argument("--external", action="store_true")
    parser.add_argument("--images-json", type=image_overrides, required=True)
    parser.add_argument("--upgrade-images-json", type=image_overrides, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-base", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--volume-root", type=Path,
                        help="create private bind-backed Docker volumes on this filesystem")
    parser.add_argument("--burst-spans", type=int, default=128, help="spans in the authenticated acceptance burst")
    args = parser.parse_args()
    if not args.aiv.is_file() or not os.access(args.aiv, os.X_OK):
        parser.error("--aiv must point to an executable candidate CLI")
    if not 1 <= args.burst_spans <= 4096:
        parser.error("--burst-spans must be between 1 and 4096")
    if set(args.images_json) != set(IMAGE_VARS):
        parser.error("--images-json must supply all five application images")
    if not any(args.images_json[role] != image for role, image in args.upgrade_images_json.items()):
        parser.error("--upgrade-images-json must change at least one image reference")
    def interrupted(signum, _frame):
        raise InterruptedError(f"interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        parser.error("--out must be empty")
    namespace = "aiv-compose-smoke-" + uuid.uuid4().hex[:12]
    runtime = Path(tempfile.mkdtemp(prefix=namespace + "-", dir=args.runtime_base)).resolve()
    runtime.chmod(0o700)
    compose_name = ("compose.external-" + args.backend if args.external else
                    "compose.seekdb" if args.backend == "seekdb" else "compose") + ".yaml"
    values = dict(ports(), NAMESPACE=namespace, OB_BACKEND=args.backend,
                  OB_MANAGED="false" if args.external else "true", OB_TENANT="aivision",
                  OB_TENANT_NAME="aivision", OB_HOST_PORT="2881",
                  OB_USER="root" if args.backend == "seekdb" else "root@aivision",
                  OB_MODE="mini", OB_MEMORY_LIMIT="6G", OB_DATAFILE_SIZE="2G", OB_LOG_DISK_SIZE="4G",
                  OB_IMAGE=os.environ.get("SEEKDB_IMAGE", SEEKDB_IMAGE) if args.backend == "seekdb"
                  else os.environ.get("OCEANBASE_IMAGE", "oceanbase/oceanbase-ce:latest"),
                  IMAGE_PULL="missing", DOCKER_NETWORK=namespace + "-net",
                  BIND_ADDR="127.0.0.1", PUBLIC_HOST="127.0.0.1",
                  AIV_STATE_DIR=str(runtime / "state"), AIV_LOG_DIR=str(runtime / "logs"),
                  SIGNOZ_ROOT_EMAIL=namespace + "@example.invalid", SIGNOZ_ROOT_ORG_NAME=namespace,
                  AIVISION_NATIVE_TRACE_HISTORY_START=datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z"))
    values.update({key: namespace + "-" + service for service, key in ROLE_VARS.items()})
    values["OB_HOST"] = namespace + "-external-database" if args.external else values["OB_CONTAINER"]
    values.update({key: secrets.token_hex(24) for key in SECRET_VARS})
    values["SIGNOZ_USER_ROOT_PASSWORD"] = "Ci-9" + secrets.token_hex(24) + "!"
    values.update({IMAGE_VARS[key]: image for key, image in args.images_json.items()})
    env_file = runtime / "private.env"
    write_env(env_file, values)
    for directory in ("state/signoz-metastore", "logs/be-runtime", "logs/agentlens"):
        (runtime / directory).mkdir(parents=True, exist_ok=True)
    compose_file = snapshot_assets(runtime, compose_name)
    smoke = ComposeSmoke(out, runtime, values, compose_file, env_file, args.external, args.aiv.resolve())
    summary = {"status": "failed", "namespace": namespace, "backend": args.backend,
               "external": args.external, "from_images": args.images_json,
               "database_image": values["OB_IMAGE"], "stops": smoke.stop_events,
               "to_images": dict(args.images_json, **args.upgrade_images_json),
               "scope": "given image combination: first boot, retry, retained-data restart, image update, OTLP/API readback"}
    summary["deployment_assets"] = {
        str(path.relative_to(runtime / "deploy")): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((runtime / "deploy").rglob("*")) if path.is_file()
    }
    started = False
    try:
        smoke.run("compose-version", ["docker", "compose", "version"])
        if args.volume_root is not None:
            smoke.prepare_volumes(args.volume_root)
            summary["volume_root"] = str(args.volume_root.resolve())
        if args.external:
            smoke.start_external()
        started = True
        smoke.run("initial-up", smoke.lifecycle_command("up"), timeout=1500)
        summary["initial_health"] = smoke.healthy("initial")
        summary["startup_resources"] = smoke.resource_snapshot("startup")
        summary["initial_database"] = smoke.sql("initial-database", write=True)
        baseline = smoke.identity("initial")
        summary["identity"] = baseline
        summary["from_image_ids"] = smoke.images("initial")
        trace = TraceCanary(smoke, namespace)
        trace.ingest(span_count=args.burst_spans)
        summary["initial_trace"] = trace.read()
        summary["burst_resources"] = smoke.resource_snapshot("burst")
        summary["dependency_recovery"] = smoke.recover_dependency(trace, namespace)
        if args.backend == "seekdb":
            smoke.stop_services("wrong-password")
            original_password = values["OB_TENANT_PASSWORD"]
            wrong_password = secrets.token_hex(24)
            smoke.private.append(wrong_password)
            values["OB_TENANT_PASSWORD"] = wrong_password
            write_env(env_file, values)
            try:
                rejected = smoke.run("wrong-password-up", smoke.lifecycle_command("up"),
                                     timeout=600, check=False)
                if rejected.returncode == 0:
                    raise AssertionError("seekdb deployment accepted a changed password for existing data")
                if args.external:
                    rejection_log = smoke.run("wrong-password-database-logs", [
                        *smoke.command, "logs", "--no-color", "database-init"]).stdout
                    expected_rejection = "SQL 1045"
                else:
                    rejection_log = rejected.stdout
                    expected_rejection = "existing seekdb volume rejected password"
                if expected_rejection not in rejection_log:
                    raise AssertionError("wrong-password deployment failed without the expected authentication rejection")
            finally:
                values["OB_TENANT_PASSWORD"] = original_password
                write_env(env_file, values)
            summary["wrong_password_rejected"] = True
            smoke.run("credential-recovery-up", smoke.lifecycle_command("up"), timeout=1500)
            summary["credential_recovery_health"] = smoke.healthy("credential-recovery")
            summary["credential_recovery_database"] = smoke.sql("credential-recovery-database")
            summary["credential_recovery_trace"] = trace.read()
            if smoke.identity("credential-recovery") != baseline:
                raise AssertionError("credential recovery changed org or persistent mount identities")
        smoke.stop_services("retry")
        smoke.run("retry-up", smoke.lifecycle_command("up"), timeout=1500)
        summary["retry_health"] = smoke.healthy("retry")
        summary["retry_database"] = smoke.sql("retry-database")
        summary["retry_trace"] = trace.read()
        if smoke.identity("retry") != baseline:
            raise AssertionError("retry changed org or persistent mount identities")
        smoke.stop_services("retained")
        smoke.run("retained-down", smoke.lifecycle_command("down"), timeout=300)
        smoke.run("retained-up", smoke.lifecycle_command("up"), timeout=1500)
        smoke.healthy("retained")
        summary["retained_database"] = smoke.sql("retained-database")
        summary["retained_trace"] = trace.read()
        if smoke.identity("retained") != baseline:
            raise AssertionError("down/up changed org or persistent mount identities")
        values.update({IMAGE_VARS[key]: image for key, image in args.upgrade_images_json.items()})
        write_env(env_file, values)
        smoke.stop_services("upgrade")
        smoke.run("upgrade-up", smoke.lifecycle_command("up"), timeout=1500)
        summary["upgrade_health"] = smoke.healthy("upgrade")
        summary["to_image_ids"] = smoke.images("upgrade")
        if summary["from_image_ids"] == summary["to_image_ids"]:
            raise AssertionError("upgrade did not change any running application image ID")
        summary["upgrade_database"] = smoke.sql("upgrade-database")
        summary["upgrade_trace"] = trace.read()
        if smoke.identity("upgrade") != baseline:
            raise AssertionError("upgrade changed org or persistent mount identities")
        smoke.stop_services("final")
        summary["status"] = "passed"
    except Exception as error:
        summary["error"] = smoke.sanitize(error)
        if started:
            try:
                smoke.run("failure-logs", [*smoke.command, "logs", "--no-color", "--tail", "80"], check=False)
            except Exception:
                pass
    finally:
        owns_resources = (started or smoke.fixture_containers or smoke.fixture_volumes or
                          smoke.fixture_networks or smoke.owned_volumes or smoke.volume_data)
        summary["cleanup_errors"] = smoke.cleanup() if owns_resources else []
        try:
            shutil.rmtree(runtime)
        except OSError as error:
            summary["cleanup_errors"].append(smoke.sanitize(error))
        if summary["cleanup_errors"]:
            summary["status"] = "failed"
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Compose smoke {summary['status']}; summary: {out / 'summary.json'}")
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Accept explicit ``aiv cast --target compose`` on a native Docker host.

Run each managed/external seekdb/OceanBase case separately. A supplied CLI and
all five application images are required; cached source images are supported.
This is source-candidate evidence, not a published-release or model-quality claim.
The frontend replacement must have a different content ID. No source blueprint,
legacy health substitution or automatic adoption of old CLI resources is used.

Credentials and immutable bundles remain in a separate private runtime directory.
Only sanitized command output and a summary are written to --out. --volume-root
can place this run's private bind-backed named volumes on a daemon-local disk.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
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
import urllib.request
import uuid

from seekdb_deploy_smoke import (
    COMPONENTS, CONTAINER_VARS, IMAGE_VARS, OCEANBASE_IMAGE, SECRET_VARS, SEEKDB_IMAGE,
    Smoke, TraceCanary, ports, private_vars,
)


ROOT = Path(__file__).resolve().parents[2]
ROLE_VARS = dict(zip(COMPONENTS, CONTAINER_VARS))
APPLICATIONS = COMPONENTS[1:]
FIXTURE_READY = """import json,sys,time,pymysql
options=json.load(sys.stdin)
for attempt in range(200):
    try:
        with pymysql.connect(host=options['host'],port=2881,user=options['user'],password=options['password'],
                             connect_timeout=3,read_timeout=3,write_timeout=3) as connection:
            with connection.cursor() as cursor: cursor.execute('SELECT 1')
        break
    except pymysql.err.OperationalError:
        if attempt==199: raise RuntimeError('external fixture SQL readiness timed out') from None
        time.sleep(3)
print('external fixture SQL ready')
"""
RUNTIME_CLEANUP = """import os,stat,sys
# Open every directory relative to its already-open parent, without following
# symlinks. A link inside this one mounted runtime is unlinked, never traversed.
flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
def empty(directory):
    for name in os.listdir(directory):
        entry=os.stat(name,dir_fd=directory,follow_symlinks=False)
        if stat.S_ISDIR(entry.st_mode):
            child=os.open(name,flags,dir_fd=directory)
            try: empty(child)
            finally: os.close(child)
            os.rmdir(name,dir_fd=directory)
        else:
            os.unlink(name,dir_fd=directory)
root=os.open(sys.argv[1],flags)
try: empty(root)
finally: os.close(root)
"""


def private_runtime_cleanup_command(runtime, namespace, image):
    """Build a root-container cleanup limited to one harness-owned random path.

    The caller must first verify scoped Docker cleanup and retention assertions.
    This function is also usable for a separately recorded cleanup receipt after
    a completed run retained its runtime because host permissions blocked removal.
    """
    runtime = Path(runtime)
    if (not re.fullmatch(r"aiv-compose-cli-(?:seekdb|oceanbase)-[0-9a-f]{12}", namespace) or
            not runtime.is_absolute() or runtime.is_symlink() or not runtime.is_dir() or
            runtime.resolve() != runtime or
            not re.fullmatch(re.escape(namespace) + r"-[A-Za-z0-9_-]+", runtime.name)):
        raise ValueError("refusing cleanup outside the exact owned random runtime directory")
    if not image or image.startswith("-") or any(character.isspace() for character in image):
        raise ValueError("cleanup requires the trusted cached diagnosis image")
    return ["docker", "run", "--rm", "--network", "none", "--user", "0:0", "--pull", "never", "--mount",
            f"type=bind,source={runtime},target=/cleanup-runtime", "--entrypoint", "python", image,
            "-c", RUNTIME_CLEANUP, "/cleanup-runtime"]


def image_mapping(value):
    try:
        data = json.loads(value) if value.lstrip().startswith("{") else json.loads(Path(value).read_text())
    except (OSError, ValueError) as error:
        raise argparse.ArgumentTypeError("expected an image JSON object or readable JSON file") from error
    if (not isinstance(data, dict) or set(data) - IMAGE_VARS.keys() or
            any(not isinstance(image, str) or not image.strip() for image in data.values())):
        raise argparse.ArgumentTypeError("images must map signoz/gateway/backend/agentlens/frontend to references")
    return data


def checked_services(inspected, namespace, expected):
    """Require every serving container's native Docker health and project identity."""
    selected = {}
    for container in inspected:
        labels = container.get("Config", {}).get("Labels") or {}
        role = labels.get("com.docker.compose.service")
        if labels.get("com.docker.compose.project") != namespace or role not in expected or role in selected:
            raise AssertionError("Compose service inventory has foreign, unexpected or duplicate containers")
        state = container.get("State", {})
        if (not state.get("Running") or state.get("OOMKilled") or state.get("Restarting") or
                state.get("Dead") or state.get("Health", {}).get("Status") != "healthy" or
                container.get("RestartCount", 0)):
            raise AssertionError("Compose service is stopped, unhealthy, restarted or OOM killed: " + role)
        selected[role] = container
    if set(selected) != set(expected):
        raise AssertionError("Compose service inventory is incomplete")
    return selected


def persistent_mounts(containers):
    return {role: sorted((mount["Destination"], mount.get("Name") or mount["Source"])
                         for mount in container.get("Mounts", [])
                         if mount.get("Type") == "volume" or mount.get("Destination") == "/var/lib/signoz")
            for role, container in containers.items()}


def saved_project(state, namespace, status):
    """Assert the cross-language persistence contract without logging credentials."""
    paths = [state / name for name in ("vars.json", ".compose-cli.json", ".compose-state.json")]
    for path in paths:
        if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
            raise AssertionError("saved project records must be private regular files")
    variables, pointer, identity = (json.loads(path.read_text()) for path in paths)
    if pointer.get("schema") != 1 or pointer.get("status") != status:
        raise AssertionError("saved Compose project has an unexpected lifecycle status")
    bundle = Path(pointer.get("bundle", ""))
    if (not bundle.is_absolute() or bundle.resolve().parent != state.resolve() / "compose/bundles" or
            not re.fullmatch(r"[0-9a-f]{64}", bundle.name)):
        raise AssertionError("saved Compose bundle is outside this deployment")
    sidecar_path = bundle / ".cli-vars.json"
    if sidecar_path.stat().st_mode & 0o077:
        raise AssertionError("saved configuration sidecar must be private")
    sidecar = json.loads(sidecar_path.read_text())
    if (identity.get("namespace") != namespace or sidecar.get("namespace") != namespace or
            Path(identity.get("state_dir", "")).resolve() != state.resolve() or
            Path(sidecar.get("state_dir", "")).resolve() != state.resolve()):
        raise AssertionError("saved project belongs to another namespace or state directory")
    if sidecar.get("vars") != variables:
        raise AssertionError("saved variables do not match the active immutable bundle")
    if not (bundle / "compose.yaml").is_file() or (bundle / "compose.py").exists():
        raise AssertionError("saved bundle must contain native Compose configuration without a host Python controller")
    return bundle


class ComposeCLISmoke(Smoke):
    def __init__(self, out, runtime, values, cli, external, volume_root=None):
        super().__init__(out, runtime, values)
        self.cli, self.external, self.volume_root = cli, external, volume_root
        self.env = {key: value for key, value in self.env.items() if not key.startswith("COMPOSE_")}
        self.docker = self.env.get("DOCKER_BIN") or shutil.which("docker") or "docker"
        self.env["DOCKER_BIN"] = self.docker
        self.python_sentinel_log = runtime / "host-python-invocations.log"
        sentinels = runtime / "no-host-python"
        sentinels.mkdir(mode=0o700)
        for name in ("python", "python3"):
            sentinel = sentinels / name
            sentinel.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$AIV_NO_HOST_PYTHON_LOG"\nexit 99\n')
            sentinel.chmod(0o700)
        self.env["AIV_NO_HOST_PYTHON_LOG"] = str(self.python_sentinel_log)
        self.env["PATH"] = str(sentinels) + os.pathsep + self.env.get("PATH", os.defpath)
        self.namespace = values["NAMESPACE"]
        self.state = runtime / "work" / self.namespace / "state"
        self.common = ["--namespace", self.namespace, "--workdir", str(runtime / "work")]
        self.variables = runtime / "vars.yaml"
        self.bundle, self.config, self.containers = None, None, {}
        self.services = APPLICATIONS if external else COMPONENTS
        self.fixture_container = self.namespace + "-external-database" if external else None
        self.owned_containers, self.owned_volumes, self.owned_networks = [], [], []
        self.volume_directory, self.stop_events = None, []
        self.purged, self.project_owned, self.resources_removed = False, False, False

    def cli_command(self, action, *extra):
        return [str(self.cli), action, *self.common, "--target", "compose", *extra]

    def exported_command(self, action):
        if self.bundle is None:
            raise AssertionError("no accepted Compose bundle")
        return [str(self.cli), action, "--target", "compose", "--compose-file", str(self.bundle / "compose.yaml"),
                "--compose-env-file", os.devnull]

    def compose_command(self, *arguments):
        return ["docker", "compose", "--project-name", self.namespace, "--file", str(self.bundle / "compose.yaml"),
                "--env-file", os.devnull, *arguments]

    def no_host_python(self):
        if self.python_sentinel_log.exists() and self.python_sentinel_log.read_text().strip():
            raise AssertionError("CLI or Docker lifecycle invoked a host Python interpreter")
        return {"python_and_python3_on_path": "exit-99 sentinels", "host_interpreter_invocations": 0}

    def absent(self, kind, name):
        result = self.run("absent-" + name, ["docker", kind, "inspect", "--format", "{{.Name}}", name], check=False)
        if result.returncode == 0:
            raise RuntimeError("refusing to reuse existing " + kind + ": " + name)

    def create_volume(self, name, labels):
        self.absent("volume", name)
        arguments = ["docker", "volume", "create"]
        for key, value in sorted(dict(labels, **{"io.aivision.test-run": self.namespace}).items()):
            arguments += ["--label", key + "=" + value]
        if self.volume_root:
            if self.volume_directory is None:
                self.volume_directory = Path(tempfile.mkdtemp(prefix=self.namespace + "-", dir=self.volume_root)).resolve()
                self.volume_directory.chmod(0o700)
            directory = self.volume_directory / str(len(self.owned_volumes))
            directory.mkdir()
            arguments += ["--driver", "local", "--opt", "type=none", "--opt", "o=bind", "--opt", "device=" + str(directory)]
        self.owned_volumes.append(name)
        self.run("create-volume-" + name, [*arguments, name])

    def create_network(self, name, *, internal=False, stack=False):
        self.absent("network", name)
        arguments = ["docker", "network", "create", "--label", "io.aivision.test-run=" + self.namespace]
        if stack:
            arguments += ["--label", "com.docker.compose.project=" + self.namespace,
                          "--label", "com.docker.compose.network=stack"]
        if internal:
            arguments += ["--internal"]
        self.owned_networks.append(name)
        self.run("create-network-" + name, [*arguments, name])

    def prepare(self):
        for name in [self.values[key] for key in CONTAINER_VARS]:
            self.absent("container", name)
        existing = self.run("absent-project", ["docker", "ps", "-aq", "--filter",
                           "label=com.docker.compose.project=" + self.namespace]).stdout.strip()
        if existing:
            raise RuntimeError("refusing to reuse an existing Compose project")
        self.absent("network", self.values["DOCKER_NETWORK"])
        if not self.external:
            self.absent("network", self.namespace + "_initialization")
        preview = self.runtime / "preview"
        self.run("generate-private-preview", self.cli_command("generate", "--vars-file",
                 str(self.variables), "--output", str(preview)))
        config = json.loads(self.run("private-preview-config", ["docker", "compose", "--file", str(preview / "compose.yaml"),
                            "--env-file", os.devnull, "--profile", "bootstrap", "config", "--format", "json"]).stdout)
        if config.get("name") != self.namespace:
            raise AssertionError("preview configuration selected a different Compose project")
        self.config = config
        self.project_owned = True
        for key, volume in config["volumes"].items():
            labels = dict(volume["labels"], **{"com.docker.compose.project": self.namespace,
                                             "com.docker.compose.volume": key})
            self.create_volume(volume["name"], labels)
        if self.external:
            self.start_external(preview / "bootstrap")

    def start_external(self, bootstrap):
        name, values = self.fixture_container, self.values
        self.absent("container", name)
        self.owned_containers.append(name)
        network = values["DOCKER_NETWORK"]
        self.create_network(network, stack=True)
        volume = self.namespace + "-external-data"
        self.create_volume(volume, {})
        common = ["docker", "run", "--name", name, "--label", "io.aivision.test-run=" + self.namespace,
                  "--network", network, "--stop-timeout", "120"]
        if values["OB_BACKEND"] == "oceanbase":
            config = self.namespace + "-external-config"
            self.create_volume(config, {})
            self.env.update({"OB_TENANT_NAME": values["OB_TENANT"], "MODE": "mini"})
            environment = ["MODE", "OB_TENANT_NAME", "OB_TENANT_PASSWORD", "OB_SYS_PASSWORD", "OB_MEMORY_LIMIT",
                           "OB_DATAFILE_SIZE", "OB_LOG_DISK_SIZE"]
            self.env.update({key: values[key] for key in environment if key in values})
            arguments = [argument for key in environment for argument in ("-e", key)]
            self.run("fixture-start", [*common, "-d", "-v", volume + ":/root/ob", "-v", config + ":/root/.obd/cluster",
                     "--mount", f"type=bind,source={bootstrap / 'oceanbase.sh'},target=/aiv-oceanbase.sh,readonly",
                     "--entrypoint", "/bin/sh", *arguments, values["OB_IMAGE"], "/aiv-oceanbase.sh"])
        else:
            initialization = self.namespace + "-external-init-net"
            self.create_network(initialization, internal=True)
            initializer = name + "-initialize"
            self.absent("container", initializer)
            self.owned_containers.append(initializer)
            self.env["AIV_SEEKDB_PASSWORD"] = values["OB_TENANT_PASSWORD"]
            shared = ["--ulimit", "nofile=65536:65536", "-v", volume + ":/var/lib/seekdb",
                      "--mount", f"type=bind,source={bootstrap / 'seekdb.sh'},target=/aiv-seekdb.sh,readonly",
                      "-e", "AIV_SEEKDB_PASSWORD", "--entrypoint", "/bin/sh", values["OB_IMAGE"], "/aiv-seekdb.sh"]
            self.run("fixture-initialize", ["docker", "run", "--name", initializer, "--network", initialization,
                     "--label", "io.aivision.test-run=" + self.namespace, *shared, "initialize"], timeout=1500)
            self.run("fixture-start", [*common, "-d", *shared, "serve"])
        self.run("fixture-ready", ["docker", "run", "--rm", "-i", "--network", network, "--entrypoint", "python",
                 values["AGENTLENS_IMAGE"], "-c", FIXTURE_READY], timeout=900,
                 stdin=json.dumps({"host": name, "user": values["OB_USER"], "password": values["OB_TENANT_PASSWORD"]}))

    def saved(self, status="ready"):
        self.bundle = saved_project(self.state, self.namespace, status)
        return {"status": status, "bundle_content_id": self.bundle.name,
                "private_variables": True, "cli_sidecar_matches": True}

    def healthy(self, label):
        identifiers = self.run(label + "-compose-ids", self.compose_command("ps", "--all", "--quiet", *self.services)).stdout.split()
        if not identifiers:
            raise AssertionError("Compose returned no serving containers")
        inspected = json.loads(self.run(label + "-container-health", ["docker", "inspect", *identifiers]).stdout)
        self.containers = checked_services(inspected, self.namespace, self.services)
        for role, container in self.containers.items():
            if container["Name"].lstrip("/") != self.values[ROLE_VARS[role]]:
                raise AssertionError("Compose resolved a different physical container name")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:" + self.values["FE_PORT"] + "/", timeout=15) as response:
            if response.status != 200:
                raise AssertionError("frontend HTTP probe failed")
        return {"services": list(self.services), "native_health": "healthy", "web_http_status": 200, "oom_killed": False}

    def identity(self, label):
        output = self.run(label + "-organization", ["docker", "exec", self.values["BE_CONTAINER"], "/bin/sh", "-ec", 'if [ -x /app/manage ]; then exec /app/manage "$@"; fi; exec python manage.py "$@"', "aiv-manage",
                          "signoz_integration_status", "--require", "--print-org"]).stdout
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        try:
            organization = str(uuid.UUID(lines[-1]))
        except (ValueError, IndexError):
            raise AssertionError("organization command did not return a final UUID") from None
        return {"organization_id": organization, "persistent_mounts": persistent_mounts(self.containers)}

    def image_ids(self, label, expected):
        actual = {}
        for role, reference in expected.items():
            container = self.containers[role]
            image = self.run(label + "-expected-" + role, ["docker", "image", "inspect", "--format", "{{.Id}}", reference]).stdout.strip()
            if not image or container["Image"] != image or container["Config"]["Image"] != reference:
                raise AssertionError("running content or reference differs from selected image: " + role)
            actual[role] = image
        return actual

    def retained(self, label, trace, baseline):
        result = {"health": self.healthy(label), "database": self.sql(label + "-database"), "trace": trace.read()}
        if self.identity(label) != baseline:
            raise AssertionError(label + " changed organization or persistent storage")
        result["organization_and_storage_retained"] = True
        return result

    def observe_saved(self):
        original = deepcopy(self.env)
        before = {name: (self.state / name).read_bytes() for name in ("vars.json", ".compose-cli.json", ".compose-state.json")}
        try:
            self.env.update(AIV_VAR_OB_BACKEND="invalid-ambient-backend", AIV_VAR_FE_CONTAINER="foreign-frontend",
                            AIV_VAR_FE_IMAGE="invalid.invalid/foreign/frontend:missing",
                            COMPOSE_FILE="/missing/foreign.yaml", COMPOSE_PROFILES="bootstrap", COMPOSE_PROJECT_NAME="foreign")
            self.run("saved-status-with-bogus-environment", self.cli_command("status"))
            self.run("saved-logs-with-bogus-environment", self.cli_command("logs", "be", "-n", "2"))
        finally:
            self.env = original
        if any((self.state / name).read_bytes() != value for name, value in before.items()):
            raise AssertionError("status/logs changed saved deployment configuration")
        return {"status": "passed", "ambient_aiv_and_compose_overrides_ignored": True, "saved_records_unchanged": True}

    def purge(self):
        records = {name: (self.state / name).read_bytes() for name in ("vars.json", ".compose-cli.json", ".compose-state.json")}
        directories = {Path(mount["source"]) for service in self.config["services"].values()
                       for mount in service.get("volumes", []) if mount.get("target") == "/var/lib/signoz"}
        if not directories or any(not (directory / ".aivision-compose-owner.json").is_file() for directory in directories):
            raise AssertionError("metadata ownership marker was not reserved")
        self.run("purge", self.cli_command("destroy", "--purge", "-y"), timeout=600)
        self.purged = True
        for volume in self.config["volumes"].values():
            if self.run("purge-removed-" + volume["name"], ["docker", "volume", "inspect", volume["name"]], check=False).returncode == 0:
                raise AssertionError("purge retained a named application volume")
        if any((self.state / name).read_bytes() != value for name, value in records.items()):
            raise AssertionError("purge removed or changed identity, project pointer or saved variables")
        if any(not (directory / ".aivision-compose-owner.json").is_file() or not any(
                entry.name != ".aivision-compose-owner.json" for entry in directory.iterdir()) for directory in directories):
            raise AssertionError("purge deleted bind-mounted metadata")
        return {"named_volumes_removed": True, "metadata_and_ownership_retained": True,
                "identity_pointer_and_variables_retained": True}

    def cleanup(self):
        errors = []
        if not self.project_owned and not any((self.owned_containers, self.owned_volumes, self.owned_networks)):
            return errors
        if (self.state / ".compose-cli.json").is_file() and not self.purged:
            try:
                if self.run("cleanup-cli-purge", self.cli_command("destroy", "--purge", "-y"), timeout=600, check=False).returncode:
                    errors.append("CLI cleanup could not stop/purge the project cleanly")
            except Exception as error:
                errors.append(self.sanitize(error))
        if self.fixture_container and self.fixture_container in self.owned_containers:
            try:
                started = time.monotonic()
                self.run("fixture-stop", ["docker", "stop", "--time", "120", self.fixture_container], timeout=150)
                state = json.loads(self.run("fixture-stopped", ["docker", "inspect", "--format", "{{json .State}}", self.fixture_container]).stdout)
                event = {"service": "external-database", "seconds": round(time.monotonic() - started, 3),
                         "exit_code": state["ExitCode"], "oom_killed": state["OOMKilled"]}
                self.stop_events.append(event)
                if state["Running"] or state["ExitCode"] != 0 or state["OOMKilled"]:
                    raise AssertionError("external fixture did not stop cleanly")
            except Exception as error:
                errors.append(self.sanitize(error))
        # Unique namespace ownership was checked before creation. Resolve exact
        # container IDs using its Compose label; never prune unrelated resources.
        if self.project_owned:
            try:
                ids = self.run("cleanup-project-container-ids", ["docker", "ps", "-aq", "--filter",
                               "label=com.docker.compose.project=" + self.namespace]).stdout.split()
                self.owned_containers.extend(ids)
            except Exception as error:
                errors.append(self.sanitize(error))
        networks = [*self.owned_networks]
        if self.project_owned:
            networks += [self.values["DOCKER_NETWORK"], self.namespace + "_initialization"]
        for kind, resources in (("container", self.owned_containers), ("volume", self.owned_volumes), ("network", networks)):
            for index, name in enumerate(dict.fromkeys(resources)):
                try:
                    inspect = self.run(f"cleanup-inspect-{kind}-{index}", ["docker", kind, "inspect", name], check=False)
                    if inspect.returncode:
                        continue
                    resource = json.loads(inspect.stdout)[0]
                    labels = resource.get("Config", {}).get("Labels") if kind == "container" else resource.get("Labels")
                    labels = labels or {}
                    if labels.get("io.aivision.test-run") != self.namespace and labels.get("com.docker.compose.project") != self.namespace:
                        raise RuntimeError("refusing to remove resource without this test's ownership: " + name)
                    command = ["docker", "rm", "-f", name] if kind == "container" else ["docker", kind, "rm", name]
                    self.run(f"cleanup-remove-{kind}-{index}", command, timeout=180)
                except Exception as error:
                    errors.append(self.sanitize(error))
        if self.volume_directory and not errors:
            try:
                self.run("cleanup-private-volume-files", ["docker", "run", "--rm", "--network", "none", "--mount",
                         f"type=bind,source={self.volume_directory},target=/cleanup", "--entrypoint", "python",
                         self.values["AGENTLENS_IMAGE"], "-c", "from pathlib import Path; import shutil; "
                         "[(shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()) for p in Path('/cleanup').iterdir()]"])
                self.volume_directory.rmdir()
            except Exception as error:
                errors.append(self.sanitize(error))
        self.resources_removed = not errors
        return errors

    def cleanup_private_runtime(self):
        if not self.project_owned or not self.purged or not self.resources_removed:
            raise RuntimeError("private runtime cleanup requires successful purge retention assertions and scoped Docker cleanup")
        # Use the actual previously validated diagnosis content ID, so cleanup
        # cannot pull a different image behind a mutable reference.
        image = self.containers["diagnosis"]["Image"]
        command = private_runtime_cleanup_command(self.runtime, self.namespace, image)
        self.run("cleanup-private-runtime-files", command, timeout=180, cwd=self.runtime.parent)
        self.runtime.rmdir()
        return {"trusted_container_root_helper": True, "network": "none", "mounted_paths": 1,
                "symlinks_followed": False, "empty_runtime_removed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aiv", type=Path, required=True)
    parser.add_argument("--backend", choices=("seekdb", "oceanbase"), default="seekdb")
    parser.add_argument("--external", action="store_true")
    parser.add_argument("--images-json", type=image_mapping, required=True)
    parser.add_argument("--upgrade-images-json", type=image_mapping, required=True, help="frontend-only replacement image")
    parser.add_argument("--database-image", help="override the selected database image, including cached source fixtures")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runtime-base", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--volume-root", type=Path)
    parser.add_argument("--burst-spans", type=int, default=16)
    parser.add_argument("--skip-failed-up", action="store_true", help="skip the intentionally invalid-image first attempt")
    parser.add_argument("--keep-runtime", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required")
    if not args.aiv.is_file() or not os.access(args.aiv, os.X_OK):
        parser.error("--aiv must point to an executable candidate CLI")
    if set(args.images_json) != set(IMAGE_VARS):
        parser.error("--images-json must supply all five application images")
    if set(args.upgrade_images_json) != {"frontend"} or args.upgrade_images_json["frontend"] == args.images_json["frontend"]:
        parser.error("--upgrade-images-json must select one different frontend reference")
    if not 1 <= args.burst_spans <= 4096:
        parser.error("--burst-spans must be between 1 and 4096")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        parser.error("--out must be empty")
    runtime_base = args.runtime_base.resolve()
    if runtime_base == out or runtime_base.is_relative_to(out):
        parser.error("--runtime-base must be outside the evidence directory")
    if args.volume_root and args.volume_root.resolve().is_relative_to(out):
        parser.error("--volume-root must be outside the evidence directory")

    def interrupted(signum, _frame):
        raise InterruptedError(f"interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    namespace = "aiv-compose-cli-" + args.backend + "-" + uuid.uuid4().hex[:12]
    runtime = Path(tempfile.mkdtemp(prefix=namespace + "-", dir=runtime_base)).resolve()
    runtime.chmod(0o700)
    default_database_image = os.environ.get("SEEKDB_IMAGE", SEEKDB_IMAGE) if args.backend == "seekdb" else os.environ.get("OCEANBASE_IMAGE", OCEANBASE_IMAGE)
    database_image = args.database_image or default_database_image
    values = dict(ports(), NAMESPACE=namespace, OB_BACKEND=args.backend, OB_MANAGED="false" if args.external else "true",
                  OB_IMAGE=database_image, OB_TENANT="aivision", OB_USER="root" if args.backend == "seekdb" else "root@aivision",
                  OB_HOST_PORT="2881", OB_MODE="mini", OB_MEMORY_LIMIT="6G", OB_DATAFILE_SIZE="2G", OB_LOG_DISK_SIZE="4G",
                  IMAGE_PULL="missing", DOCKER_NETWORK=namespace + "-net", BIND_ADDR="127.0.0.1", PUBLIC_HOST="127.0.0.1",
                  SIGNOZ_ROOT_EMAIL=namespace + "@example.invalid", SIGNOZ_ROOT_ORG_NAME=namespace,
                  AIVISION_NATIVE_TRACE_HISTORY_START=datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z"))
    values.update({key: namespace + "-" + role for role, key in ROLE_VARS.items()})
    values["OB_HOST"] = namespace + "-external-database" if args.external else values["OB_CONTAINER"]
    values.update({key: secrets.token_hex(24) for key in SECRET_VARS})
    values["SIGNOZ_USER_ROOT_PASSWORD"] = "Ci-9" + secrets.token_hex(24) + "!"
    values.update({IMAGE_VARS[role]: image for role, image in args.images_json.items()})
    smoke = ComposeCLISmoke(out, runtime, values, args.aiv.resolve(), args.external, args.volume_root)
    private_vars(smoke.variables, values)
    summary = {"status": "failed", "namespace": namespace, "backend": args.backend, "external": args.external,
               "validation_mode": "source candidate; cached/source images are not published-release acceptance",
               "database_image": database_image, "initial_images": args.images_json, "frontend_replacement": args.upgrade_images_json,
               "scope": "explicit Go CLI Compose, native health, SQL/OTLP retention, exported-file/CLI interoperability, image replacement, purge; no host Python",
               "external_fixture_shutdown": smoke.stop_events}
    try:
        summary["docker_server_platform"] = smoke.run("docker-info", ["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"]).stdout.strip()
        smoke.prepare()
        cast = smoke.cli_command("cast", "--vars-file", str(smoke.variables))
        if not args.skip_failed_up:
            invalid = smoke.run("intentional-first-up-failure", [*cast, "--var", "FE_IMAGE=invalid.invalid/aivision-smoke/missing:" + namespace],
                                timeout=1500, check=False)
            if invalid.returncode == 0:
                raise AssertionError("intentionally invalid first frontend image unexpectedly started")
            summary["failed_first_up"] = smoke.saved("pending")
        smoke.run("initial-explicit-compose-cast", cast, timeout=1800)
        summary["initial_saved_project"] = smoke.saved()
        summary["initial_health"] = smoke.healthy("initial")
        expected = dict(zip(APPLICATIONS, (args.images_json[role] for role in IMAGE_VARS)))
        if not args.external:
            expected["database"] = database_image
        summary["initial_content_ids"] = smoke.image_ids("initial", expected)
        summary["initial_database"] = smoke.sql("initial-database", write=True)
        baseline = smoke.identity("initial")
        summary["identity"] = baseline
        trace = TraceCanary(smoke, namespace)
        trace.ingest(args.burst_spans)
        summary["initial_trace"] = trace.read()
        smoke.run("native-compose-status", smoke.compose_command("ps", "--all"))
        smoke.run("native-compose-logs", smoke.compose_command("logs", "--tail", "2", "backend"))
        summary["saved_observation"] = smoke.observe_saved()
        smoke.run("exported-bundle-gauge", smoke.exported_command("gauge"))
        smoke.run("exported-bundle-status", smoke.exported_command("status"))
        smoke.run("exported-bundle-cast", smoke.exported_command("cast"), timeout=1800)
        smoke.saved()
        smoke.run("cli-status-after-direct-up", smoke.cli_command("status"))
        summary["exported_file_cli_roundtrip"] = smoke.retained("exported-roundtrip", trace, baseline)
        smoke.run("cli-saved-retry", smoke.cli_command("cast"), timeout=1800)
        smoke.saved()
        summary["same-image_retry"] = smoke.retained("retry", trace, baseline)
        replacement = args.upgrade_images_json["frontend"]
        smoke.run("frontend-image-upgrade", smoke.cli_command("cast", "--var", "FRONTEND_IMAGE=" + replacement), timeout=1800)
        smoke.saved()
        summary["upgraded"] = smoke.retained("upgraded", trace, baseline)
        summary["upgraded_content_ids"] = smoke.image_ids("upgrade", dict(expected, frontend=replacement))
        if summary["upgraded_content_ids"]["frontend"] == summary["initial_content_ids"]["frontend"]:
            raise AssertionError("frontend replacement changed reference without changing actual image content")
        smoke.run("nonpurge-destroy", smoke.cli_command("destroy", "-y"), timeout=600)
        smoke.saved()
        smoke.run("nonpurge-restored-cast", smoke.cli_command("cast"), timeout=1800)
        smoke.saved()
        summary["nonpurge_down_up"] = smoke.retained("restored", trace, baseline)
        summary["purge"] = smoke.purge()
        summary["no_host_python"] = smoke.no_host_python()
        summary["status"] = "passed"
    except Exception as error:
        summary["error"] = smoke.sanitize(error)
        for role in smoke.services:
            try:
                smoke.run("failure-logs-" + role, ["docker", "logs", "--tail", "80", values[ROLE_VARS[role]]], check=False)
            except Exception:
                pass
    finally:
        summary["cleanup_errors"] = smoke.cleanup()
        try:
            summary["no_host_python"] = smoke.no_host_python()
        except AssertionError as error:
            summary["cleanup_errors"].append(str(error))
        if summary["cleanup_errors"]:
            summary["status"] = "failed"
        summary["private_runtime_retained"] = args.keep_runtime or summary["status"] != "passed"
        if not summary["private_runtime_retained"]:
            try:
                summary["private_runtime_cleanup"] = smoke.cleanup_private_runtime()
            except Exception as error:
                summary["cleanup_errors"].append(smoke.sanitize(error))
                summary["status"] = "failed"
                summary["private_runtime_retained"] = True
        if summary["private_runtime_retained"]:
            print("Private runtime retained outside evidence: " + str(runtime), flush=True)
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Stage All-in-One inputs from a frozen source-compatible Daily."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/ci"))

from product_registry import PUBLIC_REPOSITORY
from public_registry import _anonymous_reader, _checked_json, _descriptor_digest, _index
from release_candidate import verify_frozen


PROFILE = "daily-source"
BASE = "docker.io/library/python@sha256:549988ff0804593d8373682ef5c0f0ceee48328abaaa2e054241c23f5c324751"
DIRS = {
    "aivision-be": {"app/": "opt/aivision/backend/", "agent-plugins/": "agent-plugins/",
                    "usr/local/bin/docker-entrypoint.sh": "opt/aivision/backend/docker-entrypoint.sh"},
    "agentlens": {"app/": "app/", "usr/local/bin/agentlens-entrypoint": "app/agentlens-entrypoint",
                  "etc/aivision/build-info.json": "opt/aivision/metadata/agentlens.json"},
    "aivision-fe": {"usr/share/nginx/html/": "usr/share/nginx/html/",
                    "etc/nginx/templates/default.conf.template": "etc/nginx/templates/default.conf.template"},
    "antcollector-gw": {"app/collector": "opt/aivision/collector/collector",
                        "etc/otelcol/config.yaml": "etc/otelcol/config.yaml",
                        "etc/aivision/build-info.json": "opt/aivision/metadata/collector.json",
                        "etc/ssl/certs/ca-certificates.crt": "opt/aivision/collector/ca-certificates.crt"},
    "signoz-ob": {"app/": "opt/aivision/signoz/",
                  "etc/ssl/certs/ca-certificates.crt": "opt/aivision/signoz/ca-certificates.crt"},
}
REQUIRED_FILES = {
    "aivision-be": ("opt/aivision/backend/manage.py", "opt/aivision/backend/docker-entrypoint.sh"),
    "agentlens": ("app/agentlens-entrypoint", "app/obschema/alembic.ini", "app/src/agentlens/main.py"),
    "aivision-fe": ("usr/share/nginx/html/index.html", "etc/nginx/templates/default.conf.template"),
    "antcollector-gw": ("opt/aivision/collector/collector", "etc/otelcol/config.yaml"),
    "signoz-ob": ("opt/aivision/signoz/signoz",),
}
PYTHON_LINKS = {"python", "python3", "python3.11"}


def candidate(directory, acceptance_path, *, expected_source):
    manifest = json.loads((directory / "release-manifest.json").read_text())
    acceptance = json.loads(acceptance_path.read_text())
    if (manifest.get("channel") != "daily" or manifest.get("source_sha") != expected_source
            or not re.fullmatch(r"\d+\.\d+\.\d+-daily\.\d{8}(?:\.\d+)?", manifest.get("product_version", ""))):
        raise ValueError("All-in-One requires a frozen Daily for the selected source checkout")
    verify_frozen(directory, acceptance, repository=PUBLIC_REPOSITORY, source_sha=expected_source,
                  build_id=manifest["build_id"])
    return manifest, acceptance


def image_configs(images, architecture):
    """Resolve accepted registry indexes to exact config IDs for one Linux platform."""
    result = {}
    for name, reference in images.items():
        repository, digest = reference.split("@")
        fetch = _anonymous_reader(repository)
        descriptors = _index(fetch("manifests", digest), digest)
        rows = [row for row in descriptors if row.get("platform", {}).get("os") == "linux"
                and row["platform"].get("architecture") == architecture]
        if len(rows) != 1:
            raise ValueError(f"expected one Daily linux/{architecture} payload for {name}")
        row = rows[0]
        manifest_digest = _descriptor_digest(row)
        child = _checked_json(fetch("manifests", manifest_digest), manifest_digest, row["size"])
        result[name] = {"reference": reference, "platform_digest": manifest_digest,
                        "config_id": _descriptor_digest(child["config"])}
    return result


def stage_export(stream, rootfs, component):
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            name = str(PurePosixPath(member.name))
            if component == "aivision-be" and name == "app/docker-entrypoint.sh":
                continue  # The installed executable is the image entrypoint.
            for source, target in DIRS[component].items():
                if name == source:
                    destination = target
                    break
                if source.endswith("/") and name.startswith(source):
                    destination = target + name[len(source):]
                    break
            else:
                continue
            if member.isdir():
                continue
            if member.issym() and component in {"aivision-be", "agentlens"}:
                # Only reconstruct the shared interpreter aliases in the Dockerfile.
                python = name.removeprefix("app/.venv/bin/")
                if python in PYTHON_LINKS and member.linkname in (
                        PYTHON_LINKS | {f"/usr/local/bin/{alias}" for alias in PYTHON_LINKS}):
                    continue
                if name == "app/.venv/lib64" and member.linkname == "lib":
                    continue
            parts = PurePosixPath(destination)
            if not member.isreg() or parts.is_absolute() or ".." in parts.parts:
                raise ValueError(f"unexpected daily image member in {component}: {member.name}")
            output = rootfs / destination
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as payload, output.open("xb") as target_stream:
                shutil.copyfileobj(payload, target_stream)
            output.chmod(member.mode & 0o777)


def validate_payload(rootfs, component):
    if any(not (rootfs / name).is_file() for name in REQUIRED_FILES[component]):
        name = "AgentLens" if component == "agentlens" else component
        raise ValueError(f"incomplete Daily image payload for {name}")
    if component == "aivision-be":
        entrypoint = rootfs / "opt/aivision/backend/docker-entrypoint.sh"
        original = entrypoint.read_bytes()
        if original.count(b"\ncd /app\n") != 1:
            raise ValueError("unexpected source backend entrypoint working directory")
        entrypoint.write_bytes(original.replace(b"\ncd /app\n", b"\ncd /opt/aivision/backend\n"))


def stage_images(rootfs, images):
    for component, identity in images.items():
        source = identity["config_id"]
        container_id = subprocess.check_output(["docker", "create", "--entrypoint", "/bin/false", source], text=True).strip()
        try:
            with subprocess.Popen(["docker", "export", container_id], stdout=subprocess.PIPE) as export:
                try:
                    stage_export(export.stdout, rootfs, component)
                finally:
                    export.stdout.close()
                if export.wait():
                    raise ValueError(f"Docker export failed for {component}")
            validate_payload(rootfs, component)
        finally:
            subprocess.run(["docker", "rm", "-v", container_id], check=True, stdout=subprocess.DEVNULL)

    # The shared helper expects the backend's ordinary /app deployment path.
    # AgentLens retains /app in this image. Relocate only the two helper paths.
    bootstrap = (ROOT / "deploy/bootstrap/backend-bootstrap.py").read_bytes()
    for original, replacement in ((b'"/app"', b'"/opt/aivision/backend"'),
                                  (b'"/app/manage"', b'"/opt/aivision/backend/manage"')):
        if original not in bootstrap:
            raise ValueError("unexpected backend bootstrap source paths")
        bootstrap = bootstrap.replace(original, replacement)
    (rootfs / "opt/aivision/backend/backend-bootstrap.py").write_bytes(bootstrap)



def stage_metadata(rootfs, directory, manifest):
    observed = rootfs / "opt/aivision/backend/aivision_backend/build-info.json"
    if json.loads(observed.read_bytes()) != manifest["component_builds"]["aivision-be"]:
        raise ValueError("backend build metadata differs from the frozen Daily")
    metadata = rootfs / "etc/aivision"
    metadata.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(observed, metadata / "build-info.json")
    shutil.copyfile(directory / "release-manifest.json", metadata / "release-manifest.json")


def package(directory, manifest, acceptance, image_archive, image_receipt, output):
    architecture = image_receipt["platform"].removeprefix("linux/")
    suffix = "-arm64" if architecture == "arm64" else ""
    cli_path = directory / f"aiv-linux-{'arm64' if suffix else 'x64'}.tar.gz"
    expected_cli = acceptance["assets"][cli_path.name]
    if cli_path.stat().st_size != expected_cli["size"] or file_digest(cli_path) != expected_cli["sha256"]:
        raise ValueError("CLI asset differs from the frozen Daily")
    with tarfile.open(cli_path, mode="r:gz") as bundle:
        [executable] = [entry for entry in bundle.getmembers() if entry.name == "aiv" and entry.isfile()]
        cli = bundle.extractfile(executable).read()
    machine = b"\xb7\x00" if suffix else b"\x3e\x00"
    if cli[:7] != b"\x7fELF\x02\x01\x01" or cli[18:20] != machine:
        raise ValueError("CLI asset does not match the All-in-One target architecture")
    if (image_archive.stat().st_size != image_receipt["archive_bytes"]
            or file_digest(image_archive) != image_receipt["archive_sha256"]):
        raise ValueError("image archive differs from the All-in-One build receipt")
    if output.exists() or not re.fullmatch(r"aiv-all-in-one:[a-z0-9][a-z0-9._-]*", image_receipt["image_tag"]):
        raise ValueError("use a fresh candidate directory and a local aiv-all-in-one image tag")
    archive_name = f"aiv-all-in-one-linux-{architecture}.tar.gz"
    product_version = manifest["product_version"]
    local_manifest = {
        "schema_version": 1, "profile": "all-in-one-preview", "platform": image_receipt["platform"],
        "release_tag": f"all-in-one-preview-v{product_version}",
        # The offline Skills installer uses this compatibility field to bind the CLI version.
        "base_release": f"delivery-v{product_version}",
        "source_candidate": {key: manifest[key] for key in ("source_repository", "source_sha", "release_tag", "build_id")},
        "archive": archive_name, **{key: image_receipt[key] for key in
                                  ("archive_sha256", "archive_bytes", "image_id", "image_tag")},
        "agent_cli": {"archive": cli_path.name, "version": product_version,
                      "archive_sha256": expected_cli["sha256"], "archive_bytes": expected_cli["size"],
                      "binary_sha256": hashlib.sha256(cli).hexdigest()},
    }
    with TemporaryDirectory(prefix="aiv-all-in-one-candidate-", dir=output.parent) as work:
        staged = Path(work) / "assets"
        staged.mkdir()
        shutil.copyfile(image_archive, staged / archive_name)
        shutil.copyfile(cli_path, staged / cli_path.name)
        manifest_path = staged / f"all-in-one-manifest{suffix}.json"
        manifest_path.write_text(json.dumps(local_manifest, indent=2, sort_keys=True) + "\n")
        checksums = "".join(f"{file_digest(path)}  {path.name}\n" for path in sorted(staged.iterdir()))
        (staged / "SHA256SUMS").write_text(checksums)
        staged.rename(output)


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

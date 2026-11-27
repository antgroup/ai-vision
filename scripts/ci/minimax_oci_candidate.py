"""Build and record the runner-local Linux amd64 MiniMax preview images."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from release_assets import DATABASES, file_sha256


PLATFORM = "linux/amd64"
PRODUCT_IMAGES = {
    "frontend": ("aivision-fe", "ai-vision/vfe", "Dockerfile"),
    "backend": ("aivision-be", "ai-vision", "aivision-py-be/Dockerfile"),
    "gateway": ("antcollector-gw", "antcollector", "Dockerfile"),
    "agentlens": ("agentlens", "agentlens", "docker/Dockerfile.selfhost"),
}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def read(*args, **kwargs):
    return run(*args, stdout=subprocess.PIPE, text=True, **kwargs).stdout.strip()


def read_image(ref):
    image, = json.loads(read("docker", "image", "inspect", ref))
    if (f"{image['Os']}/{image['Architecture']}" != PLATFORM
            or not DIGEST.fullmatch(image["Id"])):
        raise ValueError(f"Expected an inspectable {PLATFORM} image for {ref}")
    return image


def build_images(repo, out, source_sha, build_id, version, plugin_dist):
    tags = {}
    builder = f"minimax-oci-{build_id.replace('.', '-')}"
    with tempfile.TemporaryDirectory(prefix="minimax-build-", dir=out.parent) as scratch:
        scratch = Path(scratch)
        source_archive = scratch / "source.tar"
        with source_archive.open("wb") as stream:
            run("git", "archive", source_sha, "agentlens", "ai-vision", "antcollector",
                cwd=repo, stdout=stream)
        with tarfile.open(source_archive) as archive:
            archive.extractall(scratch, filter="data")
        source_archive.unlink()
        shutil.copytree(plugin_dist, scratch / "ai-vision/agent-plugins/dist")

        run("docker", "buildx", "create", "--name", builder, "--driver", "docker-container")
        try:
            for service, (component, context_path, dockerfile) in PRODUCT_IMAGES.items():
                context = scratch / context_path
                ref = f"aivision-minimax-candidate/{service}:{source_sha}-{build_id}"
                identity = read(sys.executable, "scripts/build/build_info.py", "--component", component,
                                "--source-sha", source_sha, "--build-id", build_id, "--format", "base64",
                                cwd=repo)
                labels = {
                    "org.opencontainers.image.source": "https://github.com/antgroup/ai-vision",
                    "org.opencontainers.image.revision": source_sha,
                    "org.opencontainers.image.version": version,
                    "io.aivision.build-id": build_id,
                }
                label_args = [arg for name, value in labels.items() for arg in ("--label", f"{name}={value}")]
                run("docker", "buildx", "build", "--builder", builder, "--platform", PLATFORM, "--load",
                    "--file", str(context / dockerfile), "--tag", ref,
                    "--build-arg", f"AIVISION_BUILD_INFO_BASE64={identity}", *label_args, str(context))
                found = read_image(ref)["Config"]["Labels"]
                if any(found.get(key) != value for key, value in labels.items()):
                    raise ValueError(f"Built image has unexpected labels for {service}")
                tags[service] = ref
        finally:
            run("docker", "buildx", "rm", builder)
    return tags


def upstream_images(repo, version):
    lock = json.loads((repo / "deploy/config/release-dependencies.json").read_text(encoding="utf-8"))
    signoz = lock["signoz"]["image"]
    if (lock["product_version"] != version.split("-", 1)[0]
            or not re.fullmatch(r"ghcr\.io/ob-labs/signoz-ob@sha256:[0-9a-f]{64}", signoz)):
        raise ValueError("VERSION needs a matching digest-pinned SigNoz dependency")
    return {"signoz": signoz, "seekdb": DATABASES["seekdb"], "oceanbase-ce": DATABASES["oceanbase"]}


def upstream_manifest(ref):
    repository, _, digest = ref.partition("@")
    if not DIGEST.fullmatch(digest):
        raise ValueError("Expected a digest-pinned upstream image")
    manifest = json.loads(read("docker", "buildx", "imagetools", "inspect", "--raw", ref))
    if "manifests" in manifest:
        entries = [entry for entry in manifest["manifests"] if entry.get("platform")]
        child, = (entry["digest"] for entry in entries
                  if entry["platform"]["os"] == "linux" and entry["platform"]["architecture"] == "amd64")
        if not DIGEST.fullmatch(child):
            raise ValueError(f"Invalid child digest for {ref}")
        digest = child
        manifest = json.loads(read("docker", "buildx", "imagetools", "inspect", "--raw",
                                   f"{repository}@{digest}"))
    return digest, manifest["config"]["digest"]


def build_cli(repo, out, version):
    archive = out / "aiv-linux-amd64.tar.gz"
    with tempfile.TemporaryDirectory(prefix="minimax-cli-", dir=out.parent) as scratch:
        scratch = Path(scratch)
        binary = scratch / "aiv"
        cli = repo / "aivision-cli"
        run("make", "build", f"OUTPUT={binary}", cwd=cli, env={**os.environ, "CGO_ENABLED": "0"})
        if read(str(binary), "--version") != f"aiv {version}":
            raise ValueError("Unexpected aiv CLI package version")
        with tarfile.open(archive, "w:gz") as package:
            package.add(binary, arcname="aiv")
            for path in sorted([cli / "LICENSE", cli / "deploy/kubernetes/README.md",
                                *cli.glob("deploy/kubernetes/dependencies/*.LICENSE")]):
                package.add(path, arcname=path.relative_to(cli))
    return archive


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def export_product_images(out, tags):
    archive = out / "product-images.tar.zst"
    with subprocess.Popen(["docker", "image", "save", *tags.values()], stdout=subprocess.PIPE) as save:
        try:
            run("zstd", "--quiet", "-T2", "-3", "-o", str(archive), stdin=save.stdout)
        finally:
            save.stdout.close()
        if save.wait() != 0:
            raise RuntimeError("Docker could not export the candidate product images")
    print(f"Product image archive: {archive.stat().st_size} bytes", flush=True)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (platform.system(), platform.machine()) != ("Linux", "x86_64"):
        parser.error("Candidate source must be built on a native Linux amd64 runner")
    if not re.fullmatch(r"[1-9][0-9]*\.[1-9][0-9]*", args.build_id):
        parser.error("Build ID must identify the Actions run and attempt")
    repo = Path(__file__).resolve().parents[2]
    if (read("git", "rev-parse", "HEAD", cwd=repo) != args.source_sha
            or read("git", "status", "--porcelain", cwd=repo)):
        parser.error("Build requires a clean checkout at the exact PR head")
    source_tree = read("git", "rev-parse", "HEAD^{tree}", cwd=repo)
    out = args.output.resolve()
    plugin_dist = out / "plugin-dist"
    if (out.is_relative_to(repo) or not plugin_dist.is_dir()
            or sorted(p.name for p in plugin_dist.iterdir()) != ["minimax-code"]):
        parser.error("Supply a MiniMax-only distribution outside the source checkout")
    plugins = repo / "ai-vision/agent-plugins"
    run(sys.executable, str(plugins / "scripts/verify_plugin_dist.py"), "--dist", str(plugin_dist),
        "--agent", "minimax-code", "--require-privacy")
    plugin = json.loads((plugin_dist / "minimax-code/latest.json").read_text(encoding="utf-8"))
    download, = plugin["downloads"]
    version = (repo / "VERSION").read_text().strip()
    pinned = upstream_images(repo, version)
    tags = build_images(repo, out, args.source_sha, args.build_id, version, plugin_dist)
    cli = build_cli(repo, out, version)
    images = {**tags, **pinned}
    image_details = {}
    for service, ref in images.items():
        detail = {"ref": ref, "platform": PLATFORM}
        if service in pinned:
            native_digest, config_id = upstream_manifest(ref)
            if not DIGEST.fullmatch(config_id):
                raise ValueError(f"Invalid {PLATFORM} image config ID for {ref}")
            detail.update(native_registry_digest=native_digest, image_id=config_id)
        else:
            image = read_image(ref)
            detail.update(image_id=image["Id"], size_bytes=image["Size"])
        image_details[service] = detail
    inputs = out / "inputs.json"
    write_json(inputs, {
        "source_sha": args.source_sha, "source_tree": source_tree, "release_version": version,
        "platform": PLATFORM, "plugin_sha256": download["sha256"], "cli_sha256": file_sha256(cli),
        "images": images, "image_ids": {key: value["image_id"] for key, value in image_details.items()},
    })
    details = out / "image-details.json"
    write_json(details, {"build_id": args.build_id, "backend_plugin_scope": "minimax-code-preview-only",
                         "images": image_details})
    archive = export_product_images(out, tags)
    files = [inputs, details, cli, archive, *sorted(path for path in plugin_dist.rglob("*") if path.is_file())]
    (out / "SHA256SUMS").write_text(
        "".join(f"{file_sha256(path)}  {path.relative_to(out)}\n" for path in files), encoding="utf-8")


if __name__ == "__main__":
    main()

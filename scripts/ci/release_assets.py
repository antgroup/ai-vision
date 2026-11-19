"""Assemble candidate release metadata and pinned CLI variables, without publishing."""

import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import tarfile

import windows_release_cli

from product_registry import product_image_repositories, validate_data_backend_mirror
from release_plan import IMAGES, require_digest
from release_reuse import validate_build

DATABASES = {
    "oceanbase": "oceanbase/oceanbase-ce@sha256:eaa2c3b3513fa21e4a658f197c1deb93c9d6b6eb742cfa5ef0f085f82b9cec54",
    "seekdb": "oceanbase/seekdb@sha256:4db4961869d554313520d6081fca29265ba6da6db7313311bd8acbed771a956b",
}
RELEASE_ASSETS = {
    f"aiv-{os_name}-{arch}.tar.gz"
    for os_name in ("linux", "darwin") for arch in ("x64", "arm64")
} | {"install-aiv.sh", "SHA256SUMS", "plugin-dist.tar.gz", "release-plan.json",
     "release-manifest.json", "release-images.vars.yaml", "release-seekdb.vars.yaml"}

IMAGE_VARS = {
    "signoz-ob": "SIGNOZ_IMAGE",
    "antcollector-gw": "GATEWAY_IMAGE",
    "aivision-be": "BE_IMAGE",
    "agentlens": "AGENTLENS_IMAGE",
    "aivision-fe": "FE_IMAGE",
}


def release_asset_names(manifest):
    """Keep publication and frozen acceptance on the same asset contract."""
    if "plugins" in manifest:
        raise ValueError("optional plugin catalogs are not part of the standard product release")
    windows = {windows_release_cli.ASSET} if windows_release_cli.included(manifest) else set()
    return RELEASE_ASSETS | windows


def assemble(directory, image_refs, *, source_sha, build_id, repository, require_windows=False):
    repositories = product_image_repositories(repository)
    plan = json.loads((directory / "release-plan.json").read_text())
    if plan["source_sha"] != source_sha or plan["build_id"] != build_id:
        raise ValueError(
            "candidate assets belong to a different source or workflow attempt; rerun all jobs"
        )
    expected = (RELEASE_ASSETS - {
        "release-manifest.json", "release-images.vars.yaml", "release-seekdb.vars.yaml",
    }) | {
        "signoz-image.txt",
        "component-plan.json",
        "data-backend-mirror.json",
    }
    if require_windows:
        expected.add(windows_release_cli.ASSET)
    mirror_path = directory / "data-backend-mirror.json"
    if {path.name for path in directory.iterdir()} != expected:
        raise ValueError(
            "release directory must contain exactly the expected CLI, plugin and plan assets"
        )
    for name in expected:
        if not (directory / name).is_file() or not (directory / name).stat().st_size:
            raise ValueError(f"missing or empty release asset: {name}")
        if name.endswith(".tar.gz"):
            with tarfile.open(directory / name, "r:gz") as archive:
                if not archive.getmembers():
                    raise ValueError(f"empty archive: {name}")
    if require_windows:
        windows_release_cli.verify_zip(directory / windows_release_cli.ASSET)
    images = {
        name: require_digest((image_refs / f"{name}.txt").read_text().strip())
        for name in IMAGES
    }
    upstream = require_digest(
        (directory / "signoz-image.txt").read_text().strip()
    )
    mirror = json.loads(mirror_path.read_text())
    deployment_ref = mirror.get("mirror_ref") if isinstance(mirror, dict) else None
    validate_data_backend_mirror(mirror, deployment_ref)
    if mirror["upstream_ref"] != upstream:
        raise ValueError("data backend mirror differs from the selected upstream digest")
    images["signoz-ob"] = deployment_ref
    component_plan = json.loads((directory / "component-plan.json").read_text())
    if set(component_plan) != set(IMAGES):
        raise ValueError("component plan must identify all owned images")
    builds = {}
    inputs = {}
    for name in IMAGES:
        builds[name] = validate_build(name, json.loads((image_refs / f"{name}.build-info.json").read_text()))
        if builds[name] != component_plan[name]["identity"]:
            raise ValueError("native build identity differs from the component plan")
        inputs[name] = component_plan[name]["context_sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", inputs[name]):
            raise ValueError("component context fingerprint must be SHA256")
    for name, ref in images.items():
        if ref.split("@", 1)[0] != repositories[name]:
            raise ValueError("release service images must use their official component packages")
    manifest = {
        **{
            key: plan[key]
            for key in ("source_sha", "product_version", "build_id", "channel")
        },
        "source_repository": repository,
        "release_tag": plan["release_tag"],
        "images": images,
        "component_builds": builds,
        "component_inputs": inputs,
        "databases": DATABASES,
        "data_backend_mirror": mirror,
        "required_artifact_smoke": ["linux/amd64/oceanbase", "linux/amd64/seekdb"],
        "workflow_url": f"https://github.com/{repository}/actions/runs/{build_id.split('.')[0]}/attempts/{build_id.split('.')[1]}",
    }
    if require_windows:
        manifest["cli_platforms"] = windows_release_cli.PLATFORMS
    (directory / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    for backend, database in DATABASES.items():
        values = {key: images[name] for name, key in IMAGE_VARS.items()}
        values.update(OB_BACKEND=backend, OB_IMAGE=database,
            AIVISION_RELEASE_MANIFEST_BASE64=base64.b64encode(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            ).decode())
        # JSON scalars are valid YAML; no credentials or shell evaluation are needed.
        (
            directory
            / (
                "release-images.vars.yaml"
                if backend == "oceanbase"
                else "release-seekdb.vars.yaml"
            )
        ).write_text(
            "# Exact images tested by this release's Linux amd64 artifact smoke.\n"
            + "".join(f"{key}: {json.dumps(value)}\n" for key, value in values.items())
        )
    (directory / "signoz-image.txt").unlink()
    (directory / "component-plan.json").unlink()
    if mirror is not None:
        mirror_path.unlink()
    files = sorted(path for path in directory.iterdir() if path.name != "SHA256SUMS")
    (directory / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in files
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--image-refs", type=Path, required=True)
    parser.add_argument("--require-windows", action="store_true")
    args = parser.parse_args()
    assemble(
        args.directory,
        args.image_refs,
        source_sha=os.environ["GITHUB_SHA"],
        build_id=f"{os.environ['GITHUB_RUN_ID']}.{os.environ['GITHUB_RUN_ATTEMPT']}",
        repository=os.environ["GITHUB_REPOSITORY"],
        require_windows=args.require_windows,
    )


if __name__ == "__main__":
    main()

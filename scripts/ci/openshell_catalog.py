"""Assemble the optional, digest-pinned OpenShell installation catalog.

Local callers supply actual built plugin and worker digests. The catalog
resolves native upstream children from NVIDIA's fixed 0.1.6 release.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess


PLATFORMS = {"linux/amd64", "linux/arm64"}
ROLES = {"plugin", "worker", "gateway", "runtime", "supervisor"}
DIGEST = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,450}@sha256:[a-f0-9]{64}\Z")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\Z")


def validate(catalog, *, product_version=None):
    if not isinstance(catalog, dict) or set(catalog) != {
        "schema_version",
        "product_version",
        "provider_version",
        "protocol_version",
        "images",
    }:
        raise ValueError("invalid OpenShell catalog fields")
    if (
        type(catalog["schema_version"]) is not int
        or catalog["schema_version"] != 1
        or type(catalog["protocol_version"]) is not int
        or catalog["protocol_version"] != 1
        or catalog["provider_version"] != "0.1.6"
        or not isinstance(catalog["product_version"], str)
        or not VERSION.fullmatch(catalog["product_version"])
        or product_version is not None
        and catalog["product_version"] != product_version
    ):
        raise ValueError("incompatible OpenShell catalog version")
    images = catalog["images"]
    if not isinstance(images, dict) or not images or set(images) - PLATFORMS:
        raise ValueError("unsupported or empty OpenShell platforms")
    for roles in images.values():
        if not isinstance(roles, dict) or set(roles) != ROLES:
            raise ValueError("all five OpenShell image roles are required")
        for ref in roles.values():
            if not isinstance(ref, str) or not DIGEST.fullmatch(ref):
                raise ValueError("OpenShell images require immutable digests")
    return catalog


def native_child(ref, platform):
    result = subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            ref,
            "--format",
            "{{json .Manifest}}",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=120,
    )
    manifest = json.loads(result.stdout)
    os_name, architecture = platform.split("/")
    children = [
        item
        for item in manifest.get("manifests", [])
        if item.get("platform", {}).get("os") == os_name
        and item.get("platform", {}).get("architecture") == architecture
    ]
    if len(children) != 1 or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", children[0].get("digest", "")
    ):
        raise ValueError(
            "the upstream image must identify exactly one native platform child"
        )
    repository = ref.split("@", 1)[0].rsplit(":", 1)[0]
    return repository + "@" + children[0]["digest"]


def assemble(plugin_refs, product_version, *, worker_refs=None):
    if (
        not isinstance(plugin_refs, dict)
        or not plugin_refs
        or set(plugin_refs) - PLATFORMS
    ):
        raise ValueError("select actual native plugin platform digests")
    if worker_refs is not None and (
        not isinstance(worker_refs, dict)
        or set(worker_refs) != set(plugin_refs)
        or any(
            not isinstance(ref, str) or not DIGEST.fullmatch(ref)
            for ref in worker_refs.values()
        )
    ):
        raise ValueError("worker images must match the selected native platforms")
    images = {}
    for platform, plugin in sorted(plugin_refs.items()):
        if not isinstance(plugin, str) or not DIGEST.fullmatch(plugin):
            raise ValueError("plugin requires an actual built digest")
        images[platform] = {
            "plugin": plugin,
            "worker": worker_refs[platform] if worker_refs is not None else plugin,
            **{
                role: native_child(
                    "ghcr.io/nvidia/openshell/" + repository + ":0.1.6", platform
                )
                for role, repository in (
                    ("gateway", "gateway"),
                    ("runtime", "sandbox"),
                    ("supervisor", "supervisor"),
                )
            },
        }
    return validate(
        {
            "schema_version": 1,
            "product_version": product_version,
            "provider_version": "0.1.6",
            "protocol_version": 1,
            "images": images,
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-refs", type=Path, required=True)
    parser.add_argument("--worker-refs", type=Path)
    parser.add_argument("--product-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    catalog = assemble(
        json.loads(arguments.plugin_refs.read_text()),
        arguments.product_version,
        worker_refs=(
            json.loads(arguments.worker_refs.read_text())
            if arguments.worker_refs is not None
            else None
        ),
    )
    arguments.output.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()

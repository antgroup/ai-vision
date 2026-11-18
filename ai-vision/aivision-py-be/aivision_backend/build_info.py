"""Report observed image identity separately from the installed product release."""

import base64
import binascii
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

BUILD_INFO_PATH = Path(__file__).resolve().with_name("build-info.json")
MAX_BUILD_INFO_BYTES = 16 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}")
VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
)
SOURCE_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)
CHANNELS = frozenset({"dev", "daily", "rc", "stable", "unknown"})
MAX_RELEASE_MANIFEST_BYTES = 64 * 1024
OWNED_IMAGES = frozenset({"aivision-fe", "aivision-be", "antcollector-gw", "agentlens"})
# Source identity in historical installed manifests, read locally for diagnostics.
HISTORICAL_PRODUCT_REPOSITORY = "ob-labs/ai-vision-ob"
PUBLIC_PRODUCT_REPOSITORY = "antgroup/ai-vision"
UPSTREAM_SIGNOZ_IMAGE = "ghcr.io/ob-labs/signoz-ob"
PUBLIC_IMAGES = {
    "aivision-fe": "ghcr.io/antgroup/ai-vision/frontend",
    "aivision-be": "ghcr.io/antgroup/ai-vision/backend",
    "antcollector-gw": "ghcr.io/antgroup/ai-vision/ingest-gateway",
    "agentlens": "ghcr.io/antgroup/ai-vision/diagnosis",
    "signoz-ob": "ghcr.io/antgroup/ai-vision/data-backend",
}
BUILD_FIELDS = (
    "schema_version",
    "component",
    "product_version",
    "component_version",
    "source_sha",
    "build_id",
    "built_at",
    "target_channel",
    "dirty",
)


def _identifier(value):
    if isinstance(value, str) and value != "unknown" and IDENTIFIER.fullmatch(value):
        return value
    return None


def _timestamp(value):
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def read_build_info():
    """Read only the image's fixed file and expose validated, allowlisted fields."""
    result = {
        "schema_version": 1,
        "component": "aivision-be",
        "product_version": None,
        "component_version": None,
        "source_sha": None,
        "build_id": None,
        "built_at": None,
        "target_channel": "unknown",
        "dirty": None,
    }
    try:
        with BUILD_INFO_PATH.open("rb") as source:
            raw = source.read(MAX_BUILD_INFO_BYTES + 1)
        if len(raw) > MAX_BUILD_INFO_BYTES:
            return result
        metadata = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return result

    if (
        not isinstance(metadata, dict)
        or type(metadata.get("schema_version")) is not int
        or metadata["schema_version"] != 1
        or metadata.get("component") != "aivision-be"
    ):
        return result

    for field in ("product_version", "component_version"):
        value = metadata.get(field)
        if isinstance(value, str) and VERSION_PATTERN.fullmatch(value):
            result[field] = value
    result["build_id"] = _identifier(metadata.get("build_id"))
    source_sha = metadata.get("source_sha")
    if isinstance(source_sha, str) and SOURCE_SHA.fullmatch(source_sha):
        result["source_sha"] = source_sha
    result["built_at"] = _timestamp(metadata.get("built_at"))
    channel = metadata.get("target_channel")
    if isinstance(channel, str) and channel in CHANNELS:
        result["target_channel"] = channel
    if type(metadata.get("dirty")) is bool:
        result["dirty"] = metadata["dirty"]
    return result


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate metadata key")
        value[key] = item
    return value


def _complete_build(metadata, component):
    """Accept a clean historical build without claiming it was built for this release."""
    if not isinstance(metadata, dict) or any(
        key not in metadata for key in BUILD_FIELDS
    ):
        return None
    if (
        type(metadata["schema_version"]) is not int
        or metadata["schema_version"] != 1
        or metadata["component"] != component
        or metadata["dirty"] is not False
        or not isinstance(metadata["product_version"], str)
        or not VERSION_PATTERN.fullmatch(metadata["product_version"])
        or (
            metadata["component_version"] is not None
            and (
                not isinstance(metadata["component_version"], str)
                or not VERSION_PATTERN.fullmatch(metadata["component_version"])
            )
        )
        or not isinstance(metadata["source_sha"], str)
        or not SOURCE_SHA.fullmatch(metadata["source_sha"])
        or _identifier(metadata["build_id"]) is None
        or _timestamp(metadata["built_at"]) is None
        or not isinstance(metadata["target_channel"], str)
        or metadata["target_channel"] not in CHANNELS - {"unknown"}
    ):
        return None
    return {field: metadata[field] for field in BUILD_FIELDS}


def _pinned_images(values, repositories):
    if not isinstance(values, dict) or set(values) != set(repositories):
        return False
    return all(
        isinstance(values[name], str)
        and re.fullmatch(re.escape(repository) + r"@sha256:[a-f0-9]{64}", values[name])
        for name, repository in repositories.items()
    )


def _installed_image_repositories(manifest):
    images = {name: f"ghcr.io/ob-labs/{name}" for name in OWNED_IMAGES | {"signoz-ob"}}
    databases = {"oceanbase": "oceanbase/oceanbase-ce", "seekdb": "oceanbase/seekdb"}
    delivery = manifest.get("delivery")
    if delivery is None:
        # Some installed legacy manifests predate source_repository.
        source = manifest.get("source_repository", HISTORICAL_PRODUCT_REPOSITORY)
        if source == PUBLIC_PRODUCT_REPOSITORY:
            return PUBLIC_IMAGES, databases
        if source == HISTORICAL_PRODUCT_REPOSITORY:
            return images, databases
        return None
    version = manifest.get("product_version")
    if (
        not isinstance(delivery, dict)
        or set(delivery) != {"repository", "release_tag", "catalog_sha"}
        or delivery.get("repository") != "ob-labs/ai-vision-skills"
        or manifest.get("source_repository") != HISTORICAL_PRODUCT_REPOSITORY
        or manifest.get("channel") != "daily"
        or not isinstance(version, str)
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-daily\.[0-9]{8}(?:\.[1-9][0-9]*)?", version)
        or delivery.get("release_tag") != "delivery-v" + version
        or not isinstance(delivery.get("catalog_sha"), str)
        or not re.fullmatch(r"[a-f0-9]{40}", delivery["catalog_sha"])
    ):
        return None
    selections = (
        (manifest.get("image_platforms"), {"linux/amd64", "linux/arm64"}),
        (manifest.get("cli_platforms"), {"linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"}),
    )
    for values, allowed in selections:
        if (
            not isinstance(values, list) or not values
            or any(not isinstance(value, str) or value not in allowed for value in values)
            or len(values) != len(set(values))
        ):
            return None
    for platform in manifest["image_platforms"]:
        archive = "linux-x64" if platform == "linux/amd64" else "linux-arm64"
        if archive not in manifest["cli_platforms"]:
            return None
    namespace = "ghcr.io/ob-labs/ai-vision-skills"
    images = {name: f"{namespace}/{name}" for name in images}
    selected_databases = manifest.get("databases")
    if not isinstance(selected_databases, dict):
        return None
    for name, component in (("oceanbase", "oceanbase-ce"), ("seekdb", "seekdb")):
        reference = selected_databases.get(name)
        mirror = f"{namespace}/{component}"
        if isinstance(reference, str) and reference.startswith(mirror + "@sha256:"):
            databases[name] = mirror
    return images, databases


def read_installed_release():
    """Read deployment metadata, never overwrite or infer the responding image's build."""
    encoded = os.environ.get("AIVISION_RELEASE_MANIFEST_BASE64", "")
    if not encoded or len(encoded) > 4 * ((MAX_RELEASE_MANIFEST_BYTES + 2) // 3):
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
        if (
            len(raw) > MAX_RELEASE_MANIFEST_BYTES
            or base64.b64encode(raw).decode() != encoded
        ):
            return None
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (binascii.Error, UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(manifest, dict):
        return None
    repositories = _installed_image_repositories(manifest)
    if repositories is None:
        return None
    version, channel = manifest.get("product_version"), manifest.get("channel")
    source = manifest.get("source_sha")
    if (
        not isinstance(version, str)
        or not VERSION_PATTERN.fullmatch(version)
        or not isinstance(channel, str)
        or channel not in CHANNELS - {"unknown"}
        or not isinstance(source, str)
        or not SOURCE_SHA.fullmatch(source)
        or _identifier(manifest.get("build_id")) is None
        or not _pinned_images(
            manifest.get("images"),
            repositories[0],
        )
        or not _pinned_images(
            manifest.get("databases"),
            repositories[1],
        )
    ):
        return None
    if manifest.get("source_repository") == PUBLIC_PRODUCT_REPOSITORY:
        mirror_ref = manifest["images"]["signoz-ob"]
        digest = mirror_ref.split("@", 1)[1]
        if manifest.get("data_backend_mirror") != {
            "upstream_ref": f"{UPSTREAM_SIGNOZ_IMAGE}@{digest}",
            "mirror_ref": mirror_ref,
        }:
            return None
    builds = manifest.get("component_builds")
    if not isinstance(builds, dict) or set(builds) != OWNED_IMAGES:
        return None
    components = {name: _complete_build(builds[name], name) for name in OWNED_IMAGES}
    if any(value is None for value in components.values()):
        return None
    return {
        "product_version": version,
        "channel": channel,
        "source_sha": source,
        "build_id": manifest["build_id"],
        "components": {name: components[name] for name in ("aivision-fe", "aivision-be")},
    }


@csrf_exempt
@never_cache
@require_GET
def version_info(request):
    """Report the responding build and the separately configured installed release."""
    return JsonResponse(
        {
            "code": 0,
            "message": "success",
            "data": {
                "backend": read_build_info(),
                "observed_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "scope": "responding_instance",
                "expected": read_installed_release(),
            },
        }
    )

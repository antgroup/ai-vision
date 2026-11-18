"""Mirror the reviewed data backend and check public release bytes in GHCR."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from product_registry import PUBLIC_REPOSITORY, product_image_repositories, validate_data_backend_mirror
from release_plan import require_digest, require_platforms


INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")
IMAGE_TYPES = ("application/vnd.oci.image.manifest.v1+json",
               "application/vnd.docker.distribution.manifest.v2+json")
MANIFEST_TYPES = ", ".join((*INDEX_TYPES, *IMAGE_TYPES, "application/vnd.oci.artifact.manifest.v1+json"))
LIMIT = 8 * 1024 * 1024
SOURCE_SIGNOZ_FIXTURE = (
    "ghcr.io/ob-labs/signoz-ob@sha256:"
    "40a59a7dd2917c34d985d72dfc1e4f649603b0f7eaddf9709f6437ab2f8ddaaf"
)


def source_fixtures(manifest, *, use_data_backend=False):
    """Select the precision fixture or the run's mirrored data backend."""
    if manifest.get("source_repository") != PUBLIC_REPOSITORY:
        raise ValueError("source fixtures require a public product candidate")
    images = manifest["images"]
    _public_image_repositories(images)
    database = require_digest(manifest["databases"]["oceanbase"])
    if database.split("@", 1)[0] != "oceanbase/oceanbase-ce":
        raise ValueError("source fixtures require the official OceanBase CE image")
    signoz = SOURCE_SIGNOZ_FIXTURE
    if use_data_backend:
        signoz = images["signoz-ob"]
        validate_data_backend_mirror(manifest.get("data_backend_mirror"), signoz)
    return {"oceanbase": database, "signoz": signoz,
            "gateway": images["antcollector-gw"], "backend": images["aivision-be"],
            "agentlens": images["agentlens"], "frontend": images["aivision-fe"]}


def _read(url, *, accept, token=None):
    request = Request(url, headers={"Accept": accept})
    if token:
        # GHCR can redirect blob downloads. Keep the registry's bearer token at GHCR.
        request.add_unredirected_header("Authorization", "Bearer " + token)
    with urlopen(request, timeout=30) as response:
        body = response.read(LIMIT + 1)
    if len(body) > LIMIT:
        raise ValueError("GHCR metadata exceeds the release verification limit")
    return body


def _anonymous_reader(repository):
    path = repository.removeprefix("ghcr.io/")
    query = urlencode({"service": "ghcr.io", "scope": f"repository:{path}:pull"})
    token = json.loads(_read(f"https://ghcr.io/token?{query}", accept="application/json")).get("token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token, re.ASCII):
        raise ValueError(f"GHCR did not issue an anonymous pull token for {repository}")

    def fetch(kind, selector):
        return _read(f"https://ghcr.io/v2/{path}/{kind}/{selector}", token=token,
                     accept=MANIFEST_TYPES if kind == "manifests" else "application/octet-stream")

    return fetch


def _checked_json(body, digest, size=None):
    if "sha256:" + hashlib.sha256(body).hexdigest() != digest:
        raise ValueError(f"registry bytes differ from their expected digest: {digest}")
    if size is not None and (type(size) is not int or size != len(body)):
        raise ValueError(f"registry bytes differ from their descriptor size: {digest}")
    return json.loads(body)


def _index(body, digest):
    document = _checked_json(body, digest)
    if document.get("schemaVersion") != 2 or document.get("mediaType") not in INDEX_TYPES:
        raise ValueError("public image digest must identify a multi-platform image index")
    require_platforms(document)
    return document["manifests"]


def _descriptor_digest(descriptor):
    digest = descriptor.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("invalid registry descriptor digest")
    if type(descriptor.get("size")) is not int or not 0 < descriptor["size"] <= LIMIT:
        raise ValueError("invalid registry descriptor size")
    return digest


def _verify_image(repository, expected):
    fetch = _anonymous_reader(repository)
    for row in _index(fetch("manifests", expected), expected):
        digest = _descriptor_digest(row)
        child = _checked_json(fetch("manifests", digest), digest, row["size"])
        platform = row.get("platform", {})
        if platform.get("os") != "linux":
            continue
        if child.get("schemaVersion") != 2 or child.get("mediaType") not in IMAGE_TYPES:
            raise ValueError(f"native descriptor must identify an image manifest: {digest}")
        config = child["config"]
        config_digest = _descriptor_digest(config)
        identity = _checked_json(fetch("blobs", config_digest), config_digest, config["size"])
        if (identity.get("os"), identity.get("architecture")) != ("linux", platform["architecture"]):
            raise ValueError(f"image config platform differs from its index descriptor: {digest}")


def _public_image_repositories(images):
    repositories = product_image_repositories(PUBLIC_REPOSITORY)
    if set(images) != set(repositories) or any(
        require_digest(images[name]).split("@", 1)[0] != repository
        for name, repository in repositories.items()
    ):
        raise ValueError("public release must use exactly its five approved packages")
    return repositories


def verify_public_images(images):
    """Read all five public indexes, attached manifests, and Linux native configs."""
    repositories = _public_image_repositories(images)
    errors = []
    for name, repository in repositories.items():
        try:
            _verify_image(repository, images[name].split("@", 1)[1])
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f"{repository}: {exc}")
    if errors:
        raise ValueError("Public image verification failed. All five packages require anonymous pull access.\n"
                         + "\n".join(errors))


def verify_public_tag(tagged_ref, expected_ref):
    """Verify a release tag through anonymous access and hash the exact index bytes."""
    repository, expected = require_digest(expected_ref).split("@", 1)
    tagged_repository, _, tag = tagged_ref.rpartition(":")
    if (repository not in product_image_repositories(PUBLIC_REPOSITORY).values()
            or repository != tagged_repository or not re.fullmatch(r"[\w][\w.-]{0,127}", tag, re.ASCII)):
        raise ValueError("public tag must identify the approved image repository")
    fetch = _anonymous_reader(repository)
    _index(fetch("manifests", tag), expected)


def _regctl(*args, missing=False):
    result = subprocess.run(["regctl", *args], capture_output=True, timeout=900, check=False)
    if result.returncode:
        message = result.stderr.decode(errors="replace")
        if missing and re.search(r"\b(?:404|not found)\b", message, re.IGNORECASE):
            return b""
        raise ValueError(f"regctl {args[0]} {args[1]} failed: {message.strip()}")
    return result.stdout


def _referrers(repository, digests):
    result = {}
    for digest in digests:
        descriptors = json.loads(_regctl(
            "artifact", "list", repository + "@" + digest, "--format", "{{json .Descriptors}}",
        ))
        if descriptors is None:
            descriptors = []  # regctl's no-referrer fallback uses a nil slice.
        if not isinstance(descriptors, list):
            raise ValueError("invalid OCI referrer list")
        result[digest] = {_descriptor_digest(descriptor) for descriptor in descriptors}
    return result


def mirror_data_backend(upstream):
    """Only the official public workflow can copy its selected exact index into a candidate tag."""
    if os.environ.get("GITHUB_REPOSITORY") != PUBLIC_REPOSITORY:
        raise ValueError("data-backend mirroring requires the official public product repository")
    run_id, attempt = (os.environ.get(name, "") for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
    if not all(re.fullmatch(r"[1-9][0-9]*", value) for value in (run_id, attempt)):
        raise ValueError("data-backend mirroring requires the exact Actions run and attempt")
    upstream_repository, digest = require_digest(upstream).split("@", 1)
    repository = product_image_repositories(PUBLIC_REPOSITORY)["signoz-ob"]
    reference = f"{repository}@{digest}"
    record = validate_data_backend_mirror({"upstream_ref": upstream, "mirror_ref": reference}, reference)
    candidate = f"{repository}:candidate-{run_id}-{attempt}"
    original = _regctl("manifest", "get", upstream, "--format", "raw-body")
    descriptors = _index(original, digest)
    existing = _regctl("manifest", "head", candidate, missing=True).decode().strip()
    if existing and existing != digest:
        raise ValueError("refusing to overwrite a conflicting candidate data-backend tag")
    subjects = [digest, *(_descriptor_digest(descriptor) for descriptor in descriptors)]
    source_referrers = _referrers(upstream_repository, subjects)
    if not existing:
        _regctl("image", "copy", "--force-recursive", "--referrers", upstream, candidate)
    if _regctl("manifest", "get", candidate, "--format", "raw-body") != original:
        raise ValueError("mirrored data-backend index differs from the reviewed upstream bytes")
    destination_referrers = _referrers(repository, subjects)
    if any(source_referrers[subject] - destination_referrers[subject] for subject in subjects):
        raise ValueError("mirrored data-backend is missing upstream OCI referrers")
    for artifact_digest in sorted(set().union(*source_referrers.values())):
        body = _regctl("manifest", "get", f"{repository}@{artifact_digest}", "--format", "raw-body")
        _checked_json(body, artifact_digest)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("repositories")
    mirror = commands.add_parser("mirror")
    mirror.add_argument("--upstream-file", type=Path, required=True)
    mirror.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    fixtures = commands.add_parser("source-fixtures")
    fixtures.add_argument("--manifest", type=Path, required=True)
    fixtures.add_argument("--use-data-backend", action="store_true",
                          help="select the candidate's verified data-backend mirror for native smoke")
    args = parser.parse_args()
    if args.command == "repositories":
        print(json.dumps(product_image_repositories(os.environ["GITHUB_REPOSITORY"])))
    elif args.command == "mirror":
        record = mirror_data_backend(args.upstream_file.read_text().strip())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2) + "\n")
    elif args.command == "verify":
        verify_public_images(json.loads(args.manifest.read_text())["images"])
    else:
        manifest = json.loads(args.manifest.read_text())
        print(json.dumps(source_fixtures(manifest, use_data_backend=args.use_data_backend),
                         separators=(",", ":")))


if __name__ == "__main__":
    main()

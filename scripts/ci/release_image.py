"""Read build identity back from the native candidate image without starting it."""

import argparse
import base64
import io
import json
import subprocess
import tarfile


def check_identity(actual, expected):
    for key in (
        "schema_version",
        "component",
        "component_version",
        "built_at",
        "product_version",
        "source_sha",
        "build_id",
        "target_channel",
        "dirty",
    ):
        if actual.get(key) != expected[key]:
            raise ValueError(f"candidate image build identity differs: {key}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    args = parser.parse_args()
    expected = json.loads(base64.b64decode(args.identity))
    subprocess.run(["docker", "pull", args.ref], check=True)
    metadata = json.loads(
        subprocess.check_output(["docker", "image", "inspect", args.ref])
    )[0]
    if args.arch and (metadata.get("Os") != "linux" or metadata.get("Architecture") != args.arch):
        raise ValueError("candidate image does not match the selected native platform")
    labels = metadata["Config"]["Labels"]
    for name, value in {
        "org.opencontainers.image.revision": expected["source_sha"],
        "org.opencontainers.image.version": expected["product_version"],
        "io.aivision.build-id": expected["build_id"],
    }.items():
        if labels.get(name) != value:
            raise ValueError(f"candidate image label differs: {name}")
    container = subprocess.check_output(
        ["docker", "create", "--entrypoint", "true", args.ref], text=True
    ).strip()
    try:
        component = expected["component"]
        path = (
            "/usr/share/nginx/html"
            if component == "aivision-fe"
            else (
                "/app/aivision_backend/build-info.json"
                if component == "aivision-be"
                else "/etc/aivision/build-info.json"
            )
        )
        archive = subprocess.check_output(["docker", "cp", f"{container}:{path}", "-"])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            if component == "aivision-fe":
                bundles = b"\n".join(
                    tar.extractfile(item).read()
                    for item in tar.getmembers()
                    if item.isfile() and item.name.endswith(".js")
                )
                if not bundles or not all(
                    str(expected[key]).encode() in bundles
                    for key in ("source_sha", "product_version", "build_id", "built_at")
                ):
                    raise ValueError(
                        "frontend bundle does not contain the expected build identity"
                    )
            else:
                files = [item for item in tar.getmembers() if item.isfile()]
                if len(files) != 1:
                    raise ValueError("expected one build identity file")
                check_identity(json.load(tar.extractfile(files[0])), expected)
    finally:
        subprocess.run(["docker", "rm", "-v", container], check=True)


if __name__ == "__main__":
    main()

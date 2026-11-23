#!/usr/bin/env python3
"""Build an isolated Linux All-in-One from a frozen product candidate or audited 1108 archive."""

import argparse
import base64
import gzip
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from slim import POLICY as DEBUG_STRIP_POLICY, slim_rootfs


DEFAULT_ARCHITECTURE = "amd64"
BUNDLE_SHA256 = "039f7b4c23c25c12cd248e46a9d885e59c0bfc593d24cfcda4d8dfe99ade986e"
WHEELS = {
    "pylibseekdb-1.4.0.post2-cp311-cp311-manylinux_2_28_x86_64.whl":
        "3f46d826badf0635cc729d6fc8826799ed2e1db07214430853c53d1422a90a21",
    "pymysql-1.1.2-py3-none-any.whl":
        "e6b1d89711dd51f8f74b1631fe08f039e7d76cf67a42a323d3178f0f25762ed9",
}
BASE_CONFIG = "sha256:44d8f4434bcb025b8cb5c3151be689a906223e47cc3146971feccda936d2e82b"
BASE_LAYERS = (
    "sha256:9eb9a78eeb101ab215acf43ffb2a709d5fca9ca2a22178564c7ee5cc30774c60",
    "sha256:f3098f2e4408e57b4f0787f1e18511cd06ddecfad252a0f51f478088fe0052df",
    "sha256:da331f07f3e9fb1214ced70513485eefe47e1d0a647b4af8fa5afb176b40b2a7",
    "sha256:f108605b29eebcf199a536318658e173a8b1eb01172bdef33740acc5e50ee9ef",
)
RECIPE = Path(__file__).with_name("Dockerfile")
RECEIPT = "context-receipt.json"
CONFIGS = {
    "aivision-be": "1135ec14a437b7df4173ce393187ed837a8e4ea9ac0524a04d84c3f1c457d67e",
    "agentlens": "ea3239cc9756c4773d15939e3f9e7879dcf8e7879243d36b50372223120ebb60",
    "aivision-fe": "dc397b1930eeb07286624ec99f942f4bf05e29a865c15c14a6c8305dabb1274b",
    "antcollector-gw": "88ffa4aa01ad3618e4407a4864e21f3edcd983edd06772b6975fa88c6c2ee62c",
    "signoz-ob": "557087e771205b362b3911fed954ec175982417cc192f6b2f64743f5ae329076",
}
ARM64_BUNDLE_SHA256 = "1c85af3596781157136e249851c7a00ba708976b300b33a140848d7da34ad5cd"
ARM64_WHEELS = {
    "pylibseekdb-1.4.0.post2-cp311-cp311-manylinux_2_28_aarch64.whl":
        "f688bbe6a6c2157492a8c269a6adb30324f6d0cd416567068cfee02b5669541e",
    "pymysql-1.1.2-py3-none-any.whl": WHEELS["pymysql-1.1.2-py3-none-any.whl"],
}
ARM64_BASE_REFERENCE = (
    "docker.io/library/python@sha256:6458ba7e4e6e69a237c8f02ad8af42dae7ae086225f2d07b8c57cdcfe75f025b"
)
ARM64_BASE_CONFIG = "sha256:a15e67496e338e39920956cb9d63d01526f3943bfe75b0fd311462d377fd37b2"
ARM64_BASE_LAYERS = (
    "sha256:100031c9cce7b091dd637c1afa21331c3448979cc0953828264b5868ec3f225e",
    "sha256:f8111a85e9bc52eccb91547cd3cf52c0a5f8d398311461d3663cf9a021933468",
    "sha256:3c703117864a349f06e74757d557fa4dd952fee2e46e565127cf6a4214308a70",
    "sha256:a89b04f273af3a609264537416c063b1ae400031c6e6aa22e5160d1f127ebe1e",
)
ARM64_CONFIGS = {
    "aivision-be": "d258934454fb85a2cc0a4e23bcded669e0fc24c3e2fcdba070df41deb44ab54c",
    "agentlens": "cb9a832c38d11cdddd5a1f019af0d432be594b613fbf3ca220c8c5fbb8453f24",
    "aivision-fe": "47093d17dc1823035f40969c3847aebdfd77eeb959f4946fc4ae6a3cd5da2382",
    "antcollector-gw": "7bcd9e98550cbd2b5d5d975a04bff2fd382113d6e8d35e9b30ff1e99c3bc599d",
    "signoz-ob": "c99013a2cdd7443e1686fecead1f48ea930d110746c3b3438d896d561c056229",
}
PLATFORMS = {
    "amd64": {
        "bundle": BUNDLE_SHA256,
        "wheels": WHEELS,
        "configs": CONFIGS,
        "base_config": BASE_CONFIG,
        "base_layers": BASE_LAYERS,
        "image_tags": "aiv-lib64-smoke/{component}:1108-amd64",
        "elf_machine": b"\x3e\x00",
    },
    "arm64": {
        "bundle": ARM64_BUNDLE_SHA256,
        "wheels": ARM64_WHEELS,
        "configs": ARM64_CONFIGS,
        "base_config": ARM64_BASE_CONFIG,
        "base_layers": ARM64_BASE_LAYERS,
        "image_tags": "aiv-local010/{component}:0.1.0-daily.20261108-arm64",
        "elf_machine": b"\xb7\x00",
    },
}
PROBE = "usr/local/bin/aiv-http-probe"
BUILD_INFO = "etc/aivision/build-info.json"
CERTS = "etc/ssl/certs/ca-certificates.crt"
LAYER_PATHS = {
    "aivision-be": {
        5: {"app/": "opt/aivision/backend/"},
        6: {"agent-plugins/": "agent-plugins/"},
        7: {BUILD_INFO: BUILD_INFO},
        8: {PROBE: PROBE},
    },
    "agentlens": {
        5: {"app/": "app/"},
        6: {BUILD_INFO: "opt/aivision/metadata/agentlens.json"},
    },
    "aivision-fe": {
        8: {"usr/share/nginx/html/": "usr/share/nginx/html/"},
        9: {"etc/nginx/templates/": "etc/nginx/templates/"},
    },
    "antcollector-gw": {
        0: {
            "app/collector": "opt/aivision/collector/collector",
            "etc/otelcol/config.yaml": "etc/otelcol/config.yaml",
            BUILD_INFO: "opt/aivision/metadata/collector.json",
            CERTS: "opt/aivision/collector/ca-certificates.crt",
        },
    },
    "signoz-ob": {
        1: {"app/": "opt/aivision/signoz/", CERTS: "opt/aivision/signoz/ca-certificates.crt"},
    },
}


def stream_sha256(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def sha256(path):
    with path.open("rb") as stream:
        return stream_sha256(stream)


def require_hash(path, expected):
    if not path.is_file() or sha256(path) != expected:
        raise ValueError(f"expected SHA-256 {expected} for {path}")


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def staged_path(path, rules):
    for source, destination in rules.items():
        if source.endswith("/") and path.startswith(source):
            return destination + path[len(source):]
        if path == source:
            return destination
    return None


def stage_layer(stream, rules, rootfs, deduplicate=False):
    files = 0
    canonical_lib = {}
    duplicate_lib = {}
    lib_prefix = "app/.venv/lib/"
    lib64_prefix = "app/.venv/lib64/"
    with tarfile.open(fileobj=stream, mode="r|") as layer:
        for entry in layer:
            name = str(PurePosixPath(entry.name))
            destination = staged_path(name, rules)
            if destination is None or entry.isdir():
                continue
            parts = PurePosixPath(destination)
            if not entry.isreg() or parts.is_absolute() or ".." in parts.parts:
                raise ValueError(f"unexpected layer member: {entry.name}")
            if deduplicate and name.startswith(lib64_prefix):
                with layer.extractfile(entry) as source:
                    duplicate_lib[name.removeprefix(lib64_prefix)] = (entry.mode, stream_sha256(source))
                continue
            target = rootfs / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            with layer.extractfile(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(entry.mode & 0o777)
            if deduplicate and name.startswith(lib_prefix):
                canonical_lib[name.removeprefix(lib_prefix)] = (entry.mode, sha256(target))
            files += 1
    if deduplicate and (not duplicate_lib or duplicate_lib != canonical_lib):
        raise ValueError("retained ARM64 lib64 tree does not exactly match the canonical lib tree")
    if not files:
        raise ValueError(f"layer is missing required payload paths: {', '.join(rules)}")
    return len(duplicate_lib)


def select_layers(manifest, architecture):
    platform = PLATFORMS[architecture]
    images = {image["RepoTags"][0]: image for image in manifest}
    layers = {}
    for component, config in platform["configs"].items():
        tag = platform["image_tags"].format(component=component)
        image = images[tag]
        if image["Config"] != f"{config}.json":
            raise ValueError(f"unexpected {component} image config")
        for position, paths in LAYER_PATHS[component].items():
            deduplicate = architecture == "arm64" and component in ("aivision-be", "agentlens") and position == 5
            layers[image["Layers"][position]] = (component, paths, deduplicate)
    return layers


def stage_bundle(bundle, rootfs, architecture):
    removed_duplicates = {}
    with tarfile.open(bundle, mode="r|gz") as archive:
        for member in archive:
            if member.name == "manifest.json" and member.isreg():
                layers = select_layers(json.load(archive.extractfile(member)), architecture)
                break
        else:
            raise ValueError("Docker save archive is missing its manifest")
    with tarfile.open(bundle, mode="r|gz") as archive:
        configs = {f"{value}.json": value for value in PLATFORMS[architecture]["configs"].values()}
        for entry in archive:
            if entry.name in configs:
                raw = archive.extractfile(entry).read()
                if hashlib.sha256(raw).hexdigest() != configs.pop(entry.name):
                    raise ValueError(f"unexpected digest of image config {entry.name}")
                config = json.loads(raw)
                if (config["os"], config["architecture"]) != ("linux", architecture):
                    raise ValueError(f"unexpected platform for {entry.name}")
            if entry.name in layers:
                component, paths, deduplicate = layers.pop(entry.name)
                with archive.extractfile(entry) as stream:
                    duplicates = stage_layer(stream, paths, rootfs, deduplicate)
                if duplicates:
                    removed_duplicates[component] = duplicates
        if configs or layers:
            raise ValueError(f"incomplete image archive: {', '.join([*configs, *layers])}")

    entrypoint = rootfs / "opt/aivision/backend/docker-entrypoint.sh"
    original = entrypoint.read_bytes()
    if original.count(b"\ncd /app\n") != 1:
        raise ValueError("unexpected backend entrypoint layout")
    entrypoint.write_bytes(original.replace(b"\ncd /app\n", b"\ncd /opt/aivision/backend\n"))
    return removed_duplicates


def stage_executable(path, destination, architecture):
    with path.open("rb") as stream:
        header = stream.read(64)
    if (
        len(header) != 64
        or header[:7] != b"\x7fELF\x02\x01\x01"
        or header[16:18] not in (b"\x02\x00", b"\x03\x00")
        or header[18:20] != PLATFORMS[architecture]["elf_machine"]
    ):
        raise ValueError(f"expected a Linux {architecture} ELF executable: {path}")
    shutil.copyfile(path, destination)
    destination.chmod(0o755)


def inventory(context):
    files = {}
    for path in sorted(context.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symbolic link in build context: {path}")
        if path.is_dir() or path == context / RECEIPT:
            continue
        if not path.is_file():
            raise ValueError(f"non-regular file in build context: {path}")
        files[path.relative_to(context).as_posix()] = {
            "sha256": sha256(path),
            "mode": path.stat().st_mode & 0o777,
        }
    return files


def prepare(args):
    architecture = args.architecture
    if architecture == "arm64" and not args.strip_arm64:
        raise ValueError("ARM64 preparation needs --strip-arm64 in addition to --strip-amd64")
    tools = {"amd64": args.strip_amd64, "arm64": args.strip_arm64}
    platform = PLATFORMS[architecture]
    context = args.context.resolve()
    if context.exists():
        raise ValueError(f"build context already exists: {context}")
    require_hash(args.bundle, platform["bundle"])
    wheel_inputs = {wheel.name: wheel for wheel in (args.wheel, args.pymysql_wheel)}
    if wheel_inputs.keys() != platform["wheels"].keys():
        raise ValueError("the two wheels must retain their pinned PyPI filenames")
    for name, digest in platform["wheels"].items():
        require_hash(wheel_inputs[name], digest)
    context.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aiv-all-in-one-", dir=context.parent) as temp:
        staged = Path(temp) / "context"
        staged.mkdir()
        rootfs = staged / "rootfs"
        duplicates = stage_bundle(args.bundle, rootfs, architecture)
        debug_strip = slim_rootfs(rootfs, architecture, tools)
        for path, name in ((args.supervisor, "aivision-all-in-one"), (args.db_owner, "aivision-seekdb")):
            stage_executable(path, rootfs / "usr/local/bin" / name, architecture)
        wheel_dir = staged / "wheel"
        wheel_dir.mkdir()
        for name, wheel in wheel_inputs.items():
            shutil.copyfile(wheel, wheel_dir / name)
        shutil.copyfile(RECIPE, staged / "Dockerfile")
        write_json(staged / RECEIPT, {
            "archive_sha256": platform["bundle"],
            "platform": f"linux/{architecture}",
            "source_image_configs": platform["configs"],
            "wheel_sha256": platform["wheels"],
            "removed_identical_lib64_files": duplicates,
            "debug_strip": debug_strip,
            "files": inventory(staged),
        })
        staged.rename(context)
    print(context)



def preparation_source():
    root = Path(__file__).resolve().parents[2]
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def prepare_daily(args):
    from daily import BASE, PROFILE, candidate, file_digest, image_configs, stage_images, stage_metadata

    manifest, acceptance = candidate(args.daily_assets, args.acceptance, expected_source=preparation_source())
    architecture = args.architecture
    platform = PLATFORMS[architecture]
    context = args.context.resolve()
    if context.exists():
        raise ValueError(f"build context already exists: {context}")
    wheels = {wheel.name: wheel for wheel in (args.wheel, args.pymysql_wheel)}
    if wheels.keys() != platform["wheels"].keys():
        raise ValueError("the two wheels must retain their pinned PyPI filenames")
    for name, digest in platform["wheels"].items():
        require_hash(wheels[name], digest)
    images = image_configs(manifest["images"], architecture)
    base = inspect_image(args.base)
    base_layers = base["RootFS"]["Layers"]
    base_digest = BASE.partition("@")[2]
    known_python = {f"python@{base_digest}", f"library/python@{base_digest}", BASE}
    if ((base["Os"], base["Architecture"]) != ("linux", architecture)
            or not known_python.intersection(base["RepoDigests"] or [])):
        raise ValueError("use the local copy of the pinned source-compatible Python runtime")
    for name, identity in images.items():
        local = inspect_image(identity["config_id"])
        if (local["Id"], local["Os"], local["Architecture"]) != (identity["config_id"], "linux", architecture):
            raise ValueError(f"local image differs from the selected candidate platform: {name}")
        if name in {"aivision-be", "agentlens"} and local["RootFS"]["Layers"][:len(base_layers)] != base_layers:
            raise ValueError(f"source service does not share the pinned Python runtime: {name}")
    context.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aiv-all-in-one-daily-", dir=context.parent) as temp:
        staged = Path(temp) / "context"
        staged.mkdir()
        rootfs = staged / "rootfs"
        stage_images(rootfs, images)
        stage_metadata(rootfs, args.daily_assets, manifest)
        for path, name in ((args.supervisor, "aivision-all-in-one"), (args.db_owner, "aivision-seekdb")):
            destination = rootfs / "usr/local/bin" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage_executable(path, destination, architecture)
        wheel_dir = staged / "wheel"
        wheel_dir.mkdir()
        for name, wheel in wheels.items():
            shutil.copyfile(wheel, wheel_dir / name)
        shutil.copyfile(RECIPE, staged / "Dockerfile")
        write_json(staged / RECEIPT, {
            "source_profile": PROFILE,
            "source_candidate": {key: manifest[key] for key in ("source_repository", "source_sha", "release_tag",
                                                                 "product_version", "build_id")},
            "release_manifest_sha256": acceptance["assets"]["release-manifest.json"]["sha256"],
            "candidate_acceptance_sha256": file_digest(args.acceptance),
            "platform": f"linux/{architecture}",
            "source_images": images,
            "base": {"reference": args.base, "config_id": base["Id"], "layers": base_layers},
            "wheel_sha256": platform["wheels"],
            "files": inventory(staged),
        })
        staged.rename(context)
    print(context)


def package_daily(args):
    from daily import candidate, package

    manifest, acceptance = candidate(args.daily_assets, args.acceptance, expected_source=preparation_source())
    receipt_path = args.archive.with_name(args.archive.name + ".json")
    receipt = json.loads(receipt_path.read_text())
    for key in ("source_repository", "source_sha", "release_tag", "product_version", "build_id"):
        if receipt.get("source_candidate", {}).get(key) != manifest[key]:
            raise ValueError("All-in-One build receipt belongs to a different candidate")
    if receipt.get("release_manifest_sha256") != acceptance["assets"]["release-manifest.json"]["sha256"]:
        raise ValueError("All-in-One build receipt belongs to different candidate assets")
    package(args.daily_assets, manifest, acceptance, args.archive, receipt, args.output)


def canonical_tag(reference):
    # Docker drops the default registry and library namespace in RepoTags.
    if reference.startswith(("docker.io/", "index.docker.io/")):
        reference = reference.split("/", 1)[1]
    if reference.startswith("library/") and reference.count("/") == 1:
        reference = reference.removeprefix("library/")
    if ":" not in reference.rsplit("/", 1)[-1]:
        reference += ":latest"
    return reference


def verify_saved_image(archive, tag, image):
    with tarfile.open(archive, mode="r:gz") as saved:
        manifest = json.load(saved.extractfile("manifest.json"))
        if len(manifest) != 1 or manifest[0].get("RepoTags") != [tag]:
            raise ValueError(f"saved image archive does not contain only {tag}")
        config = saved.extractfile(manifest[0]["Config"]).read()
    if "sha256:" + hashlib.sha256(config).hexdigest() != image["Id"]:
        raise ValueError(f"saved image archive does not match the inspected image ID for {tag}")
    metadata = json.loads(config)
    if (
        (metadata["os"], metadata["architecture"]) != (image["Os"], image["Architecture"])
        or metadata["rootfs"]["diff_ids"] != image["RootFS"]["Layers"]
    ):
        raise ValueError(f"saved image archive does not match the inspected platform or layers for {tag}")


def save_image(reference, output, image):
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with subprocess.Popen(["docker", "image", "save", reference], stdout=subprocess.PIPE) as saved:
                with gzip.GzipFile(filename="", mode="wb", fileobj=temporary, compresslevel=6, mtime=0) as compressed:
                    shutil.copyfileobj(saved.stdout, compressed, length=1024 * 1024)
                if saved.wait() != 0:
                    raise ValueError("docker image save failed")
            temporary.close()
            verify_saved_image(temporary_path, canonical_tag(reference), image)
            temporary_path.rename(output)
        finally:
            temporary_path.unlink(missing_ok=True)


def verify_context(context):
    receipt = json.loads((context / RECEIPT).read_text(encoding="utf-8"))
    architecture = receipt.get("platform", "").removeprefix("linux/")
    if architecture not in PLATFORMS or receipt.get("platform") != f"linux/{architecture}":
        raise ValueError(f"unexpected platform in the prepared context: {context}")
    platform = PLATFORMS[architecture]
    if receipt.get("wheel_sha256") != platform["wheels"]:
        raise ValueError(f"unexpected database wheels in the prepared context: {context}")
    if receipt.get("source_profile") == "daily-source":
        if receipt.get("source_candidate", {}).get("source_sha") != preparation_source():
            raise ValueError("prepared context belongs to a different candidate source checkout")
    else:
        if (receipt.get("archive_sha256") != platform["bundle"]
                or receipt.get("source_image_configs") != platform["configs"]):
            raise ValueError(f"unexpected sources in the prepared context: {context}")
        if receipt.get("debug_strip", {}).get("policy") != DEBUG_STRIP_POLICY:
            raise ValueError("prepare the context with the current debug-strip policy")
    if receipt.get("files") != inventory(context):
        raise ValueError(f"files differ from the prepared context inventory: {context}")
    require_hash(context / "Dockerfile", sha256(RECIPE))
    for name, digest in platform["wheels"].items():
        require_hash(context / "wheel" / name, digest)
    return architecture, receipt


def inspect_image(reference):
    return json.loads(subprocess.check_output(["docker", "image", "inspect", reference]))[0]


def require_local_base(reference, architecture):
    platform = PLATFORMS[architecture]
    image = inspect_image(reference)
    if (
        image["Id"] != platform["base_config"]
        or (image["Os"], image["Architecture"]) != ("linux", architecture)
        or tuple(image["RootFS"]["Layers"]) != platform["base_layers"]
    ):
        raise ValueError(f"local base does not match the pinned 1108 linux/{architecture} Python image: {reference}")


def build(args):
    context = args.context.resolve()
    architecture, prepared = verify_context(context)
    platform = PLATFORMS[architecture]
    daily_base = prepared.get("base") if prepared.get("source_profile") == "daily-source" else None
    base_layers = tuple(daily_base["layers"]) if daily_base else platform["base_layers"]
    base_config = daily_base["config_id"] if daily_base else platform["base_config"]
    output = args.archive.resolve()
    receipt_path = output.with_name(output.name + ".json")
    if output.exists() or receipt_path.exists() or output.is_relative_to(context):
        raise ValueError(f"use a fresh archive path outside the context: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    tag = canonical_tag(args.tag)
    command = ["docker", "build", "--platform", f"linux/{architecture}", "--tag", args.tag]
    if daily_base:
        reference = args.base or daily_base["reference"]
        actual = inspect_image(reference)
        if (actual["Id"], actual["Os"], actual["Architecture"], tuple(actual["RootFS"]["Layers"])) != (
                base_config, "linux", architecture, base_layers):
            raise ValueError("local runtime base differs from the prepared candidate context")
        manifest = context / "rootfs/etc/aivision/release-manifest.json"
        require_hash(manifest, prepared["release_manifest_sha256"])
        release = base64.b64encode(manifest.read_bytes()).decode("ascii")
        command.extend(["--build-arg", f"RUNTIME_BASE={reference}",
                        "--build-arg", f"AIVISION_RELEASE_MANIFEST_BASE64={release}"])
    elif args.base:
        require_local_base(args.base, architecture)
        command.extend(["--build-arg", f"RUNTIME_BASE={args.base}"])
    elif architecture == "arm64":
        command.extend(["--build-arg", f"RUNTIME_BASE={ARM64_BASE_REFERENCE}"])
    subprocess.run([*command, str(context)], check=True)
    image = inspect_image(args.tag)
    layers = image["RootFS"]["Layers"]
    if (image["Os"], image["Architecture"]) != ("linux", architecture) or tuple(layers[:len(base_layers)]) != base_layers:
        raise ValueError(f"built image does not use the pinned linux/{architecture} Python base")
    print(f"Built {tag} {image['Id']}", flush=True)
    save_image(args.tag, output, image)
    write_json(receipt_path, {
        "archive": str(output),
        "archive_bytes": output.stat().st_size,
        "archive_sha256": sha256(output),
        "base_config": base_config,
        **({key: prepared[key] for key in ("source_candidate", "source_images", "release_manifest_sha256",
                                          "candidate_acceptance_sha256")} if daily_base else {}),
        "context_receipt_sha256": sha256(context / RECEIPT),
        "image_id": image["Id"],
        "image_bytes": image["Size"],
        "image_tag": tag,
        "platform": f"linux/{architecture}",
    })
    print(receipt_path.read_text(encoding="utf-8"), end="")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare", help="stage the offline build context")
    for argument in ("bundle", "wheel", "pymysql-wheel", "supervisor", "db-owner", "context"):
        preparation.add_argument(f"--{argument}", type=Path, required=True)
    preparation.add_argument("--architecture", choices=PLATFORMS, default=DEFAULT_ARCHITECTURE)
    preparation.add_argument("--strip-amd64", required=True, help="GNU strip supporting Linux x86-64 ELF")
    preparation.add_argument("--strip-arm64", help="GNU strip supporting Linux ARM64 ELF, required for ARM64 preparation")
    preparation.set_defaults(run=prepare)
    daily = commands.add_parser("prepare-daily", help="stage a frozen source-compatible Daily, Stable, or RC")
    for argument in ("daily-assets", "acceptance", "wheel", "pymysql-wheel", "supervisor", "db-owner", "context"):
        daily.add_argument(f"--{argument}", type=Path, required=True)
    daily.add_argument("--architecture", choices=PLATFORMS, default=DEFAULT_ARCHITECTURE)
    daily.add_argument("--base", required=True, help="local alias of the pinned source-compatible Python base")
    daily.set_defaults(run=prepare_daily)
    delivery = commands.add_parser("package-daily", help="stage the verified archive and CLI for offline Skills install")
    for argument in ("daily-assets", "acceptance", "archive", "output"):
        delivery.add_argument(f"--{argument}", type=Path, required=True)
    delivery.set_defaults(run=package_daily)
    assembly = commands.add_parser("build", help="build and save the local image")
    assembly.add_argument("--context", type=Path, required=True)
    assembly.add_argument("--tag", required=True)
    assembly.add_argument("--archive", type=Path, required=True)
    assembly.add_argument("--base", help="verified local alias of the exact pinned Python base")
    assembly.set_defaults(run=build)
    args = parser.parse_args()
    try:
        args.run(args)
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()

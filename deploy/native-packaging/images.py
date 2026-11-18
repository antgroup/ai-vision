#!/usr/bin/env python3
"""Prepare artifact-only service contexts and audit every docker-save layer.

This packaging check is not a vulnerability scanner. Upstream runtime provenance
and exact public-example approvals are explicit inputs. It detects conventional
owned source and common embedded Python, not arbitrary obfuscation in binaries.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TARGETS = {"linux/amd64": (62, "linux-gnu-x64"), "linux/arm64": (183, "linux-gnu-arm64")}
COMPONENTS = {"backend": "aivision-be", "agentlens": "agentlens"}
C_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}
SUPPORT_FILES = ("agents.json", "opencode/scripts/register-opencode-plugin.mjs",
                 "pi/scripts/configure-local-install.mjs", "deepseek/scripts/install-profile.mjs")
MAX_UNPACKED = 512 * 1024 * 1024
MAX_MEMBERS = 30000
MAX_IMAGE_MEMBERS = 200000
MAX_IMAGE_METADATA_FILE = 16 * 1024 * 1024
INLINE_PYTHON = re.compile(r"\bpython(?:\d(?:\.\d+)?)?\b[^\n]{0,512}?(?:\s-[A-Za-z]*c\b|[\"']-[A-Za-z]*c[\"']|<<)")


def is_archive(name, data):
    return (name.lower().endswith(AUDITOR.ARCHIVE_SUFFIXES)
            or data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b", b"BZh", b"\xfd7zXZ\0"))
            or data[257:262] == b"ustar")


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDITOR = load_module(REPO / "scripts/build/native_artifacts.py", "image_native_auditor")
PRODUCER = load_module(HERE / "build.py", "image_native_producer")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def decode_json(data):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("duplicate JSON key: " + name)
            result[name] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def read_json(path):
    return decode_json(Path(path).read_text())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def safe_path(name, *, archive=False):
    # tar producers conventionally prefix names with ./; normalize only that.
    if archive:
        while name.startswith("./"):
            name = name[2:]
        name = name.rstrip("/")
    if not name or name == ".":
        return "" if archive else AUDITOR._safe_path(name).as_posix()
    return AUDITOR._safe_path(name).as_posix()


def verify_input_hashes(source, recorded, current):
    if not isinstance(recorded, dict) or not recorded or set(recorded) != set(current):
        raise ValueError("compilation input file set differs from the producer receipt")
    for relative, expected in recorded.items():
        safe_path(relative)
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("invalid compilation input hash")
        path = Path(source) / relative
        if not path.is_file() or path.is_symlink() or file_hash(path) != expected or current[relative] != expected:
            raise ValueError("compilation input changed: " + relative)


def validate_build_info(info, component, revision):
    if (not isinstance(info, dict) or info.get("schema_version") != 1
            or info.get("component") != COMPONENTS[component]
            or info.get("source_sha") != revision or info.get("dirty") is not False
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision)):
        raise ValueError("build identity must identify the clean frozen source and selected component")
    for field in ("product_version", "component_version"):
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?", str(info.get(field, ""))):
            raise ValueError("invalid build version")
    if (info.get("target_channel") not in {"daily", "rc", "stable"}
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(info.get("build_id", "")))):
        raise ValueError("identified release build metadata is required")
    try:
        date = datetime.fromisoformat(info["built_at"].replace("Z", "+00:00"))
        if date.utcoffset() is None:
            raise ValueError("timezone missing")
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError("invalid build timestamp") from error


def _c_source_issues(name, data, depth=0, budget=None):
    budget = budget if budget is not None else {"bytes": 0, "members": 0}
    issues = []
    if PurePosixPath(name).suffix.lower() in C_SUFFIXES:
        issues.append(("owned-c-source", name, "owned C/C++ source is excluded from runtime images"))
    # The common auditor checks Python within archives. Also exclude handwritten C
    # at every nested level, using the same conservative inspection limits.
    if is_archive(name, data):
        if depth >= AUDITOR.MAX_ARCHIVE_DEPTH:
            return issues + [("uninspected-archive", name, "C source archive nesting limit exceeded")]
        try:
            if data.startswith(b"PK\x03\x04"):
                import zipfile
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for entry in archive.infolist():
                        if entry.is_dir():
                            continue
                        budget["bytes"] += entry.file_size
                        budget["members"] += 1
                        if budget["bytes"] > AUDITOR.MAX_ARCHIVE_BYTES or budget["members"] > AUDITOR.MAX_ARCHIVE_MEMBERS:
                            return issues + [("uninspected-archive", name, "C source archive inspection limit exceeded")]
                        issues.extend(_c_source_issues(name + "!" + entry.filename, archive.read(entry), depth + 1, budget))
            else:
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                    for entry in archive:
                        if not entry.isfile():
                            continue
                        budget["bytes"] += entry.size
                        budget["members"] += 1
                        if budget["bytes"] > AUDITOR.MAX_ARCHIVE_BYTES or budget["members"] > AUDITOR.MAX_ARCHIVE_MEMBERS:
                            return issues + [("uninspected-archive", name, "C source archive inspection limit exceeded")]
                        with archive.extractfile(entry) as stream:
                            issues.extend(_c_source_issues(name + "!" + entry.name, stream.read(), depth + 1, budget))
        except (OSError, ValueError, tarfile.TarError):
            # The common auditor already reports an unreadable archive.
            pass
    return issues


def owned_issues(name, data):
    label = name
    if is_archive(name, data) and not (name.lower().endswith(AUDITOR.ARCHIVE_SUFFIXES)
            or data.startswith((b"PK\x03\x04", b"PK\x05\x06")) or data[257:262] == b"ustar"):
        # Force compressed archive inspection even after its filename is renamed.
        label += ".tar"
    issues = list(AUDITOR._source_issues(label, data)) + _c_source_issues(name, data)
    if not data.startswith(AUDITOR.NATIVE_MAGICS):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        if INLINE_PYTHON.search(text):
            issues.append(("embedded-python", name, "embedded Python command, including interpreter flags"))
    return issues


def extract_reviewed_tar(archive_path, destination):
    destination = Path(destination)
    seen, total = set(), 0
    # Validate the complete directory before writing anything.
    with tarfile.open(archive_path, "r:*") as archive:
        entries = []
        for entry in archive.getmembers():
            metadata_issues = AUDITOR.tar_metadata_issues(entry.name, pax_headers=entry.pax_headers, uname=entry.uname, gname=entry.gname)
            if metadata_issues:
                raise ValueError("reviewed archive contains unsupported metadata: " + entry.name)
            relative = safe_path(entry.name, archive=True)
            if not relative:
                if entry.isdir():
                    continue
                raise ValueError("invalid empty archive path")
            if relative in seen or not (entry.isdir() or entry.isfile()):
                raise ValueError("archive contains duplicate, link or non-regular entry: " + relative)
            seen.add(relative)
            total += entry.size
            if total > MAX_UNPACKED or len(seen) > MAX_MEMBERS:
                raise ValueError("archive exceeds inspection limits")
            entries.append((entry, relative))
        if AUDITOR.tar_metadata_issues(str(archive_path), pax_headers=archive.pax_headers):
            raise ValueError("reviewed archive contains unsupported global metadata")
        for entry, relative in entries:
            path = destination / relative
            if entry.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(entry) as source, path.open("xb") as target:
                    shutil.copyfileobj(source, target)
                path.chmod(entry.mode & 0o777)


def verify_public_examples(provider, approvals, expected):
    if not isinstance(approvals, dict) or set(approvals) != set(expected):
        raise ValueError("public examples require approvals for the exact provider files")
    for relative, approval in approvals.items():
        safe_path(relative)
        if (not isinstance(approval, dict) or not isinstance(approval.get("reason"), str)
                or not approval["reason"].strip()):
            raise ValueError("public example requires an approval reason: " + relative)
        if file_hash(Path(provider) / relative) != approval.get("sha256"):
            raise ValueError("public example changed after approval: " + relative)


def verify_stage(source, stage, component, target):
    receipt = read_json(stage / "receipt.json")
    if (receipt.get("schema_version") != 1 or receipt.get("target") != target
            or receipt.get("inputs_stable") is not True or component not in receipt.get("components", {})):
        raise ValueError("stage lacks a successful matching central producer receipt")
    components = tuple(receipt["components"])
    if any(name not in PRODUCER.COMPONENTS for name in components):
        raise ValueError("unknown component in producer receipt")
    current = PRODUCER.snapshot_inputs(source, components)
    verify_input_hashes(source, receipt["input_sha256"], current)
    result = receipt["components"][component]
    runtime = stage / result["runtime"]
    if runtime.resolve() != (stage / component / "runtime").resolve():
        raise ValueError("unexpected runtime location")
    for name, key in (("manifest.json", "manifest_sha256"), ("source-audit.json", "source_audit_sha256")):
        if file_hash(stage / component / name) != result[key]:
            raise ValueError("stage evidence changed after producer receipt: " + name)
    manifest = read_json(stage / component / "manifest.json")
    report = AUDITOR.audit_artifact(runtime, manifest)
    if not report["ok"]:
        raise ValueError("stage content audit failed: " + json.dumps(report))
    for record in manifest["files"]:
        with (runtime / record["path"]).open("rb") as stream:
            if stream.read(4) == b"\x7fELF":
                verify_elf(runtime / record["path"], target)
        if record["owner"] == "self" and owned_issues(record["path"], (runtime / record["path"]).read_bytes()):
            raise ValueError("stage contains owned source: " + record["path"])
    return receipt, manifest, runtime


def verify_elf(path, target):
    with Path(path).open("rb") as source:
        header = source.read(20)
    if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != TARGETS[target][0]:
        raise ValueError("native executable does not match selected target: " + str(path))


def copy_file(source, destination):
    if Path(source).is_symlink() or not Path(source).is_file():
        raise ValueError("context input must be a regular file: " + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def verify_onboarding(source, stage, selected):
    receipt = read_json(stage / "receipt.json")
    if (receipt.get("inputs_stable") is not True or receipt.get("target") not in TARGETS
            or TARGETS[receipt["target"]][1] != selected or "onboarding" not in receipt.get("components", {})):
        raise ValueError("installer receipt does not match selected client target")
    verify_input_hashes(source, receipt["input_sha256"], PRODUCER.snapshot_inputs(source, tuple(receipt["components"])))
    result = receipt["components"]["onboarding"]
    archive = stage / "onboarding" / selected / "connect.tar.gz"
    info_path = archive.with_name("build-info.json")
    info = read_json(info_path)
    if (file_hash(archive) != result["sha256"] or file_hash(info_path) != result["build_info_sha256"]
            or info.get("sha256") != result["sha256"] or info.get("inputs_stable") is not True
            or info.get("target") != selected):
        raise ValueError("installer bytes differ from successful receipt")
    if owned_issues("connect.tar.gz", archive.read_bytes()):
        raise ValueError("installer archive contains source or could not be inspected")
    return archive, {"target": selected, "sha256": result["sha256"], "receipt_sha256": file_hash(stage / "receipt.json")}


def native_installer_destination(context, target):
    if target not in {value[1] for value in TARGETS.values()}:
        raise ValueError("unsupported native installer target")
    return Path(context) / "provider/native-onboarding" / target / "connect.tar.gz"


def verify_copied_runtime(runtime, manifest, replacements):
    expected = {entry["path"]: entry for entry in manifest["files"]}
    actual = AUDITOR._files(Path(runtime))
    if set(actual) != set(expected) | replacements:
        raise ValueError("copied runtime file set changed during preparation")
    for relative, path in actual.items():
        if relative not in replacements and (file_hash(path) != expected[relative]["sha256"] or path.stat().st_size != expected[relative]["size"]):
            raise ValueError("copied runtime artifact changed after stage audit: " + relative)


def prepare_context(args):
    source, stage, output = (path.resolve() for path in (args.source_root, args.stage_dir, args.output_dir))
    for protected in (source, stage, args.build_info.resolve(), args.health_probe.resolve()):
        if output == protected or output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError("context output must not overlap source or prepared inputs")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("context output must be absent or empty")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.base_image_id):
        raise ValueError("base image requires its full locally inspected config ID")
    if not args.base_image or any(character.isspace() for character in args.base_image):
        raise ValueError("invalid runtime base reference")
    try:
        git_revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        git_revision = None
    revision = args.source_revision or git_revision
    if not revision or git_revision and git_revision != revision:
        raise ValueError("source revision must match the source checkout; archives need explicit --source-revision")
    info = read_json(args.build_info)
    info_bytes = args.build_info.read_bytes()
    if json.loads(info_bytes) != info:
        raise ValueError("build metadata changed while reading")
    validate_build_info(info, args.component, revision)
    receipt, manifest, runtime = verify_stage(source, stage, args.component, args.target)
    verify_elf(args.health_probe, args.target)
    if receipt.get("source_revision") not in (None, revision):
        raise ValueError("native stage recorded a different source revision")
    output.mkdir(parents=True, exist_ok=True)
    context = output / "context"
    context.mkdir()
    shutil.copytree(runtime, context / "runtime")
    native_entry = HERE / ("native-entrypoint-" + args.component + ".sh")
    old_entry = "docker-entrypoint.sh" if args.component == "backend" else "agentlens-entrypoint"
    copy_file(native_entry, context / "runtime" / old_entry)
    (context / "runtime" / old_entry).chmod(0o755)
    copy_file(args.health_probe, context / "aiv-http-probe")
    verify_elf(context / "aiv-http-probe", args.target)
    (context / "aiv-http-probe").chmod(0o755)
    metadata = context / "metadata/build-info.json"
    metadata.parent.mkdir()
    metadata.write_bytes(info_bytes)
    if args.component == "backend":
        (context / "runtime/aivision_backend/build-info.json").write_bytes(info_bytes)
    provider_evidence, examples = {}, {}
    if args.component == "backend":
        if not all((args.plugin_dist, args.plugin_dist_sha256, args.plugin_inputs, args.public_examples, args.installer_target)):
            raise ValueError("backend requires reviewed plugin distribution, source proof, public examples and selected native installers")
        provider = source / "ai-vision/agent-plugins"
        dist = args.plugin_dist.resolve()
        if file_hash(dist) != args.plugin_dist_sha256:
            raise ValueError("reviewed plugin distribution checksum differs")
        plugin_inputs = read_json(args.plugin_inputs)
        verify_input_hashes(source, plugin_inputs, plugin_inputs)
        approvals = read_json(args.public_examples)
        expected = {path.relative_to(provider).as_posix() for directory in ("langfuse/examples", "nvidia-nemo-relay/examples")
                    for path in (provider / directory).glob("*.yaml")}
        expected.add("nvidia-nemo-relay/relay_tracing.py")
        verify_public_examples(provider, approvals, expected)
        for relative in (*SUPPORT_FILES, *sorted(expected)):
            copy_file(provider / relative, context / "provider" / relative)
        examples = {"agent-plugins/" + name: approval for name, approval in approvals.items()}
        extract_reviewed_tar(dist, context / "provider/dist")
        verifier = load_module(provider / "scripts/verify_plugin_dist.py", "image_plugin_verifier")
        catalog = read_json(provider / "agents.json")
        for plugin in catalog:
            if plugin.get("package"):
                plugin["package_name"] = read_json(provider / plugin["source"] / "package.json")["name"]
        verifier.verify(context / "provider/dist", catalog, {("linux-gnu", args.target.split("/")[1].replace("amd64", "x64"))}, require_privacy=True)
        declared = {}
        for entry in args.onboarding_stage:
            target, separator, directory = entry.partition("=")
            if not separator or target in declared:
                raise ValueError("installer stages must be distinct TARGET=DIRECTORY entries")
            declared[target] = Path(directory).resolve()
        if set(declared) != set(args.installer_target) or len(args.installer_target) != len(set(args.installer_target)):
            raise ValueError("installer stages must match the explicitly selected client targets")
        installers = []
        for target in args.installer_target:
            archive, evidence = verify_onboarding(source, declared[target], target)
            copy_file(archive, native_installer_destination(context, target))
            installers.append(evidence)
        provider_evidence = {"plugin_dist_sha256": args.plugin_dist_sha256, "plugin_source_inputs": plugin_inputs,
                             "public_example_approvals": approvals, "installer_targets": args.installer_target, "installers": installers}
    replacements = {old_entry}
    if args.component == "backend":
        replacements.add("aivision_backend/build-info.json")
    verify_copied_runtime(context / "runtime", manifest, replacements)
    verify_input_hashes(source, receipt["input_sha256"], PRODUCER.snapshot_inputs(source, tuple(receipt["components"])))
    if file_hash(args.build_info) != sha256(info_bytes):
        raise ValueError("build metadata changed during context preparation")
    image_files = {}
    stage_owners = {entry["path"]: entry["owner"] for entry in manifest["files"]}
    for path in sorted(context.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(context).as_posix()
        if path.is_symlink():
            raise ValueError("runtime context contains unreviewed symlink")
        if relative.startswith("runtime/"):
            image_path = "app/" + relative.removeprefix("runtime/")
            owner = stage_owners.get(relative.removeprefix("runtime/"), "self")
        elif relative.startswith("provider/"):
            image_path, owner = "agent-plugins/" + relative.removeprefix("provider/"), "self"
        elif relative == "aiv-http-probe":
            image_path, owner = "usr/local/bin/aiv-http-probe", "self"
        elif relative == "metadata/build-info.json":
            image_path, owner = "etc/aivision/build-info.json", "self"
        else:
            raise ValueError("unexpected context payload: " + relative)
        if image_path in examples:
            owner = "public-example"
        if owner == "self" and owned_issues(image_path, path.read_bytes()):
            raise ValueError("context contains owned source: " + image_path)
        image_files[image_path] = {"sha256": file_hash(path), "size": path.stat().st_size, "owner": owner}
    dockerfile = (HERE / ("Dockerfile.native-" + args.component)).read_text()
    (context / "Dockerfile").write_text(dockerfile.replace("ARG RUNTIME_BASE", "ARG RUNTIME_BASE=" + args.base_image, 1))
    (context / ".dockerignore").write_text("*\n!Dockerfile\n!runtime/\n!runtime/**\n!provider/\n!provider/**\n!metadata/\n!metadata/**\n!aiv-http-probe\n")
    result = {"schema_version": 1, "component": args.component, "target": args.target,
              "source_revision": revision, "source_revision_binding": "git" if git_revision else "explicit archive identity with verified compilation inputs",
              "build_info": info, "build_info_sha256": sha256(info_bytes),
              "producer_receipt_sha256": file_hash(stage / "receipt.json"), "producer_source_revision": receipt.get("source_revision"),
              "compilation_inputs": receipt["input_sha256"], "base_image": args.base_image, "base_image_id": args.base_image_id,
              "dockerfile_sha256": file_hash(context / "Dockerfile"), "image_files": image_files,
              "dependency_roots": ["app/" + name for name in manifest["dependency_roots"]],
              "owned_roots": ["app", "agent-plugins", "etc/aivision", "usr/local/bin/aiv-http-probe"],
              "public_examples": examples, "provider": provider_evidence,
              "qualification": "sanitized context and input/content checks; image layers and service acceptance are separate gates"}
    write_json(output / "receipt.json", result)
    return result


def _saved_image(archive):
    members = {}
    for entry in archive.getmembers():
        name = safe_path(entry.name, archive=True)
        if name in members:
            raise ValueError("duplicate docker-save archive member")
        if name:
            members[name] = entry
    manifest = decode_json(_image_metadata_bytes(archive, members["manifest.json"]))
    if not isinstance(manifest, list) or len(manifest) != 1:
        raise ValueError("audit requires one image per docker-save archive")
    saved = manifest[0]
    config_name = safe_path(saved["Config"])
    config_data = _image_metadata_bytes(archive, members[config_name])
    image_id = "sha256:" + sha256(config_data)
    config = decode_json(config_data)
    layers = [safe_path(name) for name in saved["Layers"]]
    if len(layers) != len(set(layers)):
        raise ValueError("duplicate image layer")
    layer_hashes = []
    for name in layers:
        digest = hashlib.sha256()
        with archive.extractfile(members[name]) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        layer_hashes.append("sha256:" + digest.hexdigest())
    if config["rootfs"]["diff_ids"] != layer_hashes:
        raise ValueError("docker-save layer bytes differ from image config diff_ids")
    return config, image_id, layers, layer_hashes, members, config_name


def _image_metadata_bytes(archive, entry):
    if not entry.isfile() or entry.size > MAX_IMAGE_METADATA_FILE:
        raise ValueError("image metadata must be a bounded regular file: " + entry.name)
    with archive.extractfile(entry) as stream:
        return stream.read(MAX_IMAGE_METADATA_FILE + 1)


def _image_member_hash(archive, entry):
    result = hashlib.sha256()
    with archive.extractfile(entry) as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            result.update(chunk)
    return result.digest()


def image_envelope_issues(archive, members, base_archive, base_members, layers, config_name):
    """Inspect save-only payloads without reclassifying exact upstream frames.

    Layer bodies have their own ownership-aware pass, and config history has its
    exact-prefix exemption. Other metadata inherited byte-for-byte from the
    reviewed base is upstream; additions and changes are product-owned.
    """
    issues = []
    for name, entry in members.items():
        if name in layers or name == config_name or entry.isdir():
            continue
        if not entry.isfile():
            issues.append(("unexpected-image-entry", name, "image metadata links and special entries are excluded"))
            continue
        previous = base_members.get(name)
        if (name != "manifest.json" and previous and previous.isfile() and previous.size == entry.size
                and _image_member_hash(archive, entry) == _image_member_hash(base_archive, previous)):
            continue
        data = _image_metadata_bytes(archive, entry)
        if name.lower().endswith(".json") or data.lstrip().startswith((b"{", b"[")):
            if metadata_has_source(decode_json(data)):
                issues.append(("embedded-python", name, "image envelope JSON contains an embedded Python program"))
        issues.extend(owned_issues(name, data))
    return issues


def metadata_has_source(value):
    """Inspect decoded leaves, including quoted programs in shell history."""
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, str):
            candidates = [current]
            try:
                candidates.extend(shlex.split(current))
            except ValueError:
                pass
            if any(AUDITOR._source_issues("image-config", candidate.encode("utf-8"))
                   or INLINE_PYTHON.search(candidate) for candidate in candidates):
                return True
    return False


def audit_image_save(image_path, base_path, receipt):
    violations, layers_report, observed = [], [], {}
    def issue(code, path, message):
        violations.append({"code": code, "path": path, "message": message})
    report = {"ok": False, "violations": violations, "layers": layers_report}
    try:
        if receipt.get("schema_version") != 1:
            raise ValueError("unsupported context receipt")
        # Read the whole outer TAR framing, including headers that tarfile
        # resolves away and bytes beyond its logical end. Payloads are skipped
        # in bounded chunks; large reviewed layer bodies are not loaded here.
        with Path(image_path).open("rb") as stream:
            raw_issues = AUDITOR.tar_stream_issues("image-save", stream, max_members=MAX_IMAGE_MEMBERS)
            for code, path, message in raw_issues:
                issue(code, path, message)
        if raw_issues:
            raise ValueError("image-save raw metadata audit failed")
        with tarfile.open(base_path, "r:*") as base_archive, tarfile.open(image_path, "r:*") as archive:
            base_config, base_id, base_layers, base_hashes, base_members, _ = _saved_image(base_archive)
            config, image_id, layers, hashes, members, config_name = _saved_image(archive)
            report.update({"image_config_sha256": image_id, "base_config_sha256": base_id,
                           "base_layers": len(base_layers), "product_layers": len(layers) - len(base_layers),
                           "target": config["os"] + "/" + config["architecture"]})
            if base_id != receipt["base_image_id"] or hashes[:len(base_hashes)] != base_hashes:
                raise ValueError("image does not retain the exact reviewed pristine runtime base")
            if report["target"] != receipt["target"] or (base_config["os"], base_config["architecture"]) != (config["os"], config["architecture"]):
                raise ValueError("image/base platform differs from selected target")
            base_history = base_config.get("history", [])
            history = config.get("history", [])
            if not isinstance(base_history, list) or not isinstance(history, list) or history[:len(base_history)] != base_history:
                raise ValueError("image history does not retain the exact reviewed base prefix")
            expected = receipt["image_files"]
            # Exempt only the identical reviewed base-history prefix. All other
            # top-level fields, extension keys and nested values are delivered
            # metadata and may carry Python without any filesystem file.
            metadata = {key: value for key, value in config.items() if key != "history"}
            metadata["history"] = history[len(base_history):]
            if metadata_has_source(metadata):
                issue("embedded-python", "image-config", "image config/history contains an embedded Python program")
            for code, path, message in image_envelope_issues(archive, members, base_archive, base_members, layers, config_name):
                issue(code, path, message)
            for index, name in enumerate(layers):
                baseline = index < len(base_layers)
                layers_report.append({"index": index, "diff_id": hashes[index], "owner": "upstream-base" if baseline else "product", "files_checked": 0})
                if baseline:
                    continue
                with archive.extractfile(members[name]) as raw_layer:
                    raw_issues = AUDITOR.tar_stream_issues("layer-" + str(index), raw_layer, max_members=MAX_IMAGE_MEMBERS)
                    for code, path, message in raw_issues:
                        issue(code, path, message)
                if raw_issues:
                    continue
                with archive.extractfile(members[name]) as layer_stream, tarfile.open(fileobj=layer_stream, mode="r|*") as layer:
                    seen = set()
                    for entry in layer:
                        for code, path, message in AUDITOR.tar_metadata_issues(entry.name, pax_headers=entry.pax_headers, uname=entry.uname, gname=entry.gname):
                            issue(code, path, message)
                        relative = safe_path(entry.name, archive=True)
                        if not relative:
                            continue
                        if relative in seen:
                            issue("duplicate-layer-path", relative, "ambiguous duplicate image layer entry")
                        seen.add(relative)
                        if entry.isdir():
                            continue
                        if PurePosixPath(relative).name.startswith(".wh."):
                            issue("unexpected-whiteout", relative, "artifact-only images must not hide files from earlier layers")
                            continue
                        if not entry.isfile():
                            issue("unexpected-layer-entry", relative, "product-added links and special files are excluded")
                            continue
                        layers_report[-1]["files_checked"] += 1
                        if entry.size > MAX_UNPACKED:
                            issue("uninspected-layer-file", relative, "file exceeds inspection limit")
                            continue
                        with layer.extractfile(entry) as stream:
                            data = stream.read(MAX_UNPACKED + 1)
                        record = expected.get(relative)
                        if record is None or record.get("sha256") != sha256(data) or record.get("size") != len(data):
                            issue("unexpected-layer-content", relative, "product layer bytes differ from reviewed sanitized context")
                        else:
                            observed[relative] = record["sha256"]
                        # Unexpected files always inherit owned status: no dependency inference by path.
                        owner = record.get("owner") if record else "self"
                        if owner == "public-example":
                            approval = receipt.get("public_examples", {}).get(relative, {})
                            if approval.get("sha256") != sha256(data) or not approval.get("reason"):
                                issue("invalid-public-example", relative, "exact approved example identity is missing")
                        elif owner != "dependency":
                            for code, path, message in owned_issues(relative, data):
                                issue(code, path, message)
                    for code, path, message in AUDITOR.tar_metadata_issues("layer-" + str(index), pax_headers=layer.pax_headers):
                        issue(code, path, message)
            for relative in sorted(set(expected) - set(observed)):
                issue("missing-image-file", relative, "reviewed context file is absent from product layers")
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        issue("invalid-image", "", str(error))
    report["ok"] = not violations
    report["qualification"] = "all base layer identities and every product-added layer file checked; no service or vulnerability claim"
    return report


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--component", choices=COMPONENTS, required=True)
    prepare.add_argument("--target", choices=TARGETS, required=True)
    prepare.add_argument("--source-root", type=Path, default=REPO)
    prepare.add_argument("--source-revision")
    prepare.add_argument("--stage-dir", type=Path, required=True)
    prepare.add_argument("--build-info", type=Path, required=True)
    prepare.add_argument("--base-image", required=True)
    prepare.add_argument("--base-image-id", required=True)
    prepare.add_argument("--health-probe", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--plugin-dist", type=Path)
    prepare.add_argument("--plugin-dist-sha256")
    prepare.add_argument("--plugin-inputs", type=Path)
    prepare.add_argument("--public-examples", type=Path)
    prepare.add_argument("--installer-target", choices=[target[1] for target in TARGETS.values()], action="append", default=[])
    prepare.add_argument("--onboarding-stage", action="append", default=[], metavar="TARGET=DIRECTORY")
    audit = commands.add_parser("audit-image")
    audit.add_argument("--image-save", type=Path, required=True)
    audit.add_argument("--base-save", type=Path, required=True)
    audit.add_argument("--receipt", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_context(args)
            print(json.dumps({"context": str(args.output_dir / "context"), "target": result["target"], "files": len(result["image_files"])}))
            return 0
        result = audit_image_save(args.image_save, args.base_save, read_json(args.receipt))
        write_json(args.output, result)
        print(json.dumps({"ok": result["ok"], "violations": result["violations"][:10]}))
        return 0 if result["ok"] else 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, tarfile.TarError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

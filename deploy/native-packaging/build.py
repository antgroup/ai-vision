#!/usr/bin/env python3
"""Prepare, compile, audit and smoke native inputs for a later image build.

Run in the target Linux CPython 3.11.14 / Bookworm compiler environment. Only
component runtime directories and the installer archive are image inputs. Work
directories and receipts are private build evidence, never runtime COPY roots.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


PYTHON_VERSION = (3, 11, 14)
TARGETS = {"linux/arm64": ("arm64", 183, "linux-gnu-arm64"),
           "linux/amd64": ("amd64", 62, "linux-gnu-x64")}
COMPONENTS = ("backend", "agentlens", "onboarding")
SERVICE_TOOLS = {"Cython": "3.2.4", "setuptools": "79.0.1"}
ONBOARDING_TOOLS = {"Nuitka": "4.3.1", "ordered-set": "4.1.0", "patchelf": "0.17.2.4", "zstandard": "0.25.0"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def overlap(first, second):
    first, second = Path(first).resolve(), Path(second).resolve()
    return first == second or first in second.parents or second in first.parents


def run(command, *, cwd=None, env=None, log=None):
    """Run argv without a shell; keep potentially private compiler output in a log."""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})
    if env:
        environment.update(env)
    command = [str(argument) for argument in command]
    if log is None:
        subprocess.run(command, cwd=cwd, env=environment, check=True)
        return
    with Path(log).open("w") as stream:
        subprocess.run(command, cwd=cwd, env=environment, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)


def probe_python(python):
    # Do not resolve virtualenv interpreter symlinks: that loses their packages.
    script = """import importlib.metadata,json,platform,sys,sysconfig
versions={}
for name in ('Cython','setuptools','Nuitka','ordered-set','patchelf','zstandard','uv'):
 try: versions[name]=importlib.metadata.version(name)
 except importlib.metadata.PackageNotFoundError: versions[name]=None
print(json.dumps({'python':list(sys.version_info[:3]),'system':platform.system(),
 'machine':platform.machine(),'soabi':sysconfig.get_config_var('SOABI'),
 'base_prefix':sys.base_prefix,'prefix':sys.prefix,'libc':platform.libc_ver(),
 'versions':versions}))
"""
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return json.loads(subprocess.check_output([str(python), "-I", "-c", script], text=True, env=environment))


def check_runtime(probe, target, label="Python"):
    architecture = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(
        probe.get("machine", "").lower())
    if (tuple(probe.get("python", ())) != PYTHON_VERSION or probe.get("system") != "Linux"
            or architecture != TARGETS[target][0]):
        raise ValueError(label + " must be target Linux CPython 3.11.14 for " + target)


def validate_paths(args):
    source, work, output = (path.resolve() for path in (args.source_root, args.work_dir, args.output_dir))
    if not source.is_dir():
        raise ValueError("source root must exist")
    for first, second in ((source, work), (source, output), (work, output)):
        if overlap(first, second):
            raise ValueError("source, private work and distribution paths must not overlap")
    for label, path in (("work", work), ("output", output)):
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise ValueError(label + " must be absent or empty; existing content is never replaced")
    protected = [args.tool_python, args.onboarding_tool_python,
                 args.backend_deps_venv, args.backend_deps_lock,
                 args.agentlens_deps_venv, args.agentlens_deps_lock]
    for path in filter(None, protected):
        if any(overlap(path, generated) for generated in (work, output)):
            raise ValueError("prepared inputs must stay outside work and output paths")
    return source, work, output


def tree_identity(root):
    """Bind prepared dependency bytes as well as the supplied lock declaration."""
    root = Path(root)
    return {path.relative_to(root).as_posix(): digest(path)
            for path in sorted(root.rglob("*")) if path.is_file()}


def validate_dependency(venv, lock, expected_lock, target):
    venv, lock, expected_lock = Path(venv), Path(lock), Path(expected_lock)
    if not venv.is_dir() or not (venv / "bin/python").is_file():
        raise ValueError("dependency environment requires bin/python")
    if lock.read_bytes() != expected_lock.read_bytes():
        raise ValueError("prepared dependency lock differs from the source uv.lock")
    probe = probe_python(venv / "bin/python")
    check_runtime(probe, target, "dependency interpreter")
    return {"venv": str(venv.absolute()), "lock": str(lock.absolute()),
            "lock_sha256": digest(lock), "python": probe, "files_sha256": tree_identity(venv)}


def copy_dependencies(source, target, project):
    """Materialize upstream runtime files and strip every owned package copy."""
    source, target = Path(source), Path(target)
    if overlap(source, target):
        raise ValueError("dependency source and destination must not overlap")
    if project not in {"backend", "agentlens"}:
        raise ValueError("unknown owned project: " + project)

    def ignore_duplicate_lib64(directory, names):
        if Path(directory) != source or sys.platlibdir != "lib" or "lib64" not in names:
            return ()
        lib, lib64 = source / "lib", source / "lib64"
        if (
            lib64.is_symlink()
            and not lib.is_symlink()
            and lib.is_dir()
            and lib64.resolve() == lib.resolve()
        ):
            return ("lib64",)
        return ()

    shutil.copytree(source, target, symlinks=False, ignore=ignore_duplicate_lib64)
    packages = ("agentlens",) if project == "agentlens" else ("apps", "aivision_backend", "config")
    distributions = ("agentlens",) if project == "agentlens" else ("aivision", "aivision_backend", "aivision-backend")
    for site in target.rglob("site-packages"):
        if not site.is_dir():
            continue
        for name in packages:
            shutil.rmtree(site / name, ignore_errors=True)
            for suffix in (".py", ".pyc", ".pyo", ".pyi"):
                (site / (name + suffix)).unlink(missing_ok=True)
        for directory in (site, site / "__pycache__"):
            for name in distributions:
                patterns = (name + "-*.dist-info", name + "*.egg-info", "*" + name + "*.pth",
                            "__editable___" + name.replace("-", "_") + "*", "_editable_impl_" + name + "*")
                for pattern in patterns:
                    for path in directory.glob(pattern):
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
        # A resolved runtime must not retain any editable project source mapping.
        if any(site.glob("__editable__*")):
            raise ValueError("dependency runtime contains an unreviewed editable install")


def snapshot_inputs(source, components):
    paths = {source / "deploy/native-packaging/build.py", source / "deploy/native-packaging/requirements-build.txt",
             source / "scripts/build/native_artifacts.py"}
    if "backend" in components:
        backend = source / "ai-vision/aivision-py-be"
        for package in ("apps", "aivision_backend", "config"):
            paths.update(path for path in (backend / package).rglob("*.py")
                         if "tests" not in path.relative_to(backend).parts)
        paths.update(path for path in (backend / "apps/core/templates").rglob("*") if path.is_file())
        paths.update(backend / name for name in ("manage.py", "docker/manage.c", "docker-entrypoint.sh", ".env.container",
                     "scripts/native_build.py", "scripts/build_native.sh", "pyproject.toml", "uv.lock",
                     "apps/core/data/models-dev-api.zip", "apps/core/data/models-dev-LICENSE",
                     "apps/core/data/models-dev-NOTICE.txt",
                     "apps/core/trace_query/langfuse/native_v1_contract.json",
                     "apps/core/fixtures/onboarding_demo.otlp"))
        if (backend / "aivision_backend/build-info.json").is_file():
            paths.add(backend / "aivision_backend/build-info.json")
        paths.update(source / "deploy/bootstrap" / name for name in ("backend-bootstrap.py", "database-init.py", "wait-http.py"))
    if "agentlens" in components:
        agentlens = source / "agentlens"
        for directory, pattern in (("src", "*.py"), ("obschema", "*.py"), ("native", "*.py"), ("native", "*.c")):
            paths.update((agentlens / directory).rglob(pattern))
        paths.update(agentlens / name for name in ("native/requirements-build.txt", "docker/selfhost-entrypoint.sh",
                     "obschema/alembic.ini", "env/env.dev", "env/env.prod", "uv.lock", "pyproject.toml"))
        paths.add(source / "deploy/bootstrap/database-init.py")
    if "onboarding" in components:
        paths.update(source / "ai-vision/agent-plugins/scripts" / name for name in ("connect", "native_config.py", "hook_runtime.py"))
        paths.add(source / "scripts/build/build_native_onboarding.py")
    return {path.relative_to(source).as_posix(): digest(path) for path in sorted(paths)}


def prepare_tools(args, work, components):
    compiler = args.tool_python
    if compiler is None:
        compiler = work / "compiler/bin/python"
        run([sys.executable, "-m", "venv", work / "compiler"], log=work / "prepare-venv.log")
        run([compiler, "-m", "pip", "install", "--disable-pip-version-check", "-r",
             args.source_root / "deploy/native-packaging/requirements-build.txt"], log=work / "prepare-tools.log")
    compiler = Path(compiler).absolute()
    onboarding = Path(args.onboarding_tool_python or compiler).absolute()
    result = {}
    for name, python, required in (("service", compiler, SERVICE_TOOLS if any(c != "onboarding" for c in components) else {}),
                                   ("onboarding", onboarding, ONBOARDING_TOOLS if "onboarding" in components else {})):
        if not required:
            continue
        probe = probe_python(python)
        check_runtime(probe, args.target, name + " compiler")
        for package, version in required.items():
            if probe["versions"].get(package) != version:
                raise ValueError(name + " compiler requires " + package + "==" + version)
        result[name] = {"python": str(python), "probe": probe}
    # uv is needed only when a runtime environment has not been supplied.
    result["resolver_python"] = str(compiler)
    return result


def prepare_dependencies(args, source, work, tools, components):
    result = {}
    for component in (name for name in components if name != "onboarding"):
        project = source / ("agentlens" if component == "agentlens" else "ai-vision/aivision-py-be")
        venv, lock = getattr(args, component + "_deps_venv"), getattr(args, component + "_deps_lock")
        if bool(venv) != bool(lock):
            raise ValueError("prepared " + component + " dependencies require both venv and lock")
        if venv is None:
            resolver = Path(tools["resolver_python"]).with_name("uv")
            if probe_python(tools["resolver_python"])["versions"].get("uv") != "0.9.30":
                raise ValueError("dependency preparation requires uv==0.9.30 in the compiler environment")
            directory = work / "dependencies" / component
            manifests = directory / "project"
            manifests.mkdir(parents=True)
            for name in ("pyproject.toml", "uv.lock", "README.md"):
                shutil.copy2(project / name, manifests / name)
            venv, lock = directory / "venv", manifests / "uv.lock"
            run([resolver, "sync", "--frozen", "--no-dev", "--no-install-project", "--python", sys.executable],
                cwd=manifests, env={"UV_PROJECT_ENVIRONMENT": str(venv), "UV_PYTHON_DOWNLOADS": "never",
                                    "UV_LINK_MODE": "copy", "UV_CACHE_DIR": str(work / "uv-cache")},
                log=work / (component + "-dependencies.log"))
        result[component] = validate_dependency(venv, lock, project / "uv.lock", args.target)
    return result


def audit_runtime(source, runtime, evidence, target):
    spec = importlib.util.spec_from_file_location("native_source_audit", source / "scripts/build/native_artifacts.py")
    auditor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auditor)
    owned = ["."]
    for site in (runtime / ".venv").rglob("site-packages"):
        for name in ("agentlens", "apps", "aivision_backend", "config"):
            owned.append((site / name).relative_to(runtime).as_posix())
            for suffix in (".py", ".pyc", ".pyo", ".pyi", ".pyw", ".pyx", ".pxd", ".pxi"):
                owned.append((site / (name + suffix)).relative_to(runtime).as_posix())
            owned.extend(path.relative_to(runtime).as_posix()
                         for path in (site / "__pycache__").glob(name + ".*.pyc"))
    manifest = auditor.make_manifest(runtime, self_owned_roots=owned,
                                    dependency_roots={".venv": "Upstream runtime resolved from the recorded unchanged uv.lock; owned project installs removed"})
    audit = auditor.audit_artifact(runtime, manifest)
    if not audit["ok"]:
        raise ValueError("runtime source-content audit failed: " + json.dumps(audit))
    elf_files = 0
    for path in runtime.rglob("*"):
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            header = stream.read(64)
        if not header.startswith(b"\x7fELF"):
            continue
        if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01" or int.from_bytes(header[18:20], "little") != TARGETS[target][1]:
            raise ValueError("runtime ELF architecture differs from requested target: " + str(path))
        elf_files += 1
    if not elf_files:
        raise ValueError("runtime has no target native artifacts")
    for name, value in (("manifest.json", manifest), ("source-audit.json", audit)):
        (evidence / name).write_text(json.dumps(value, indent=2) + "\n")
    return {"files_checked": audit["files_checked"], "elf_files": elf_files,
            "manifest_sha256": digest(evidence / "manifest.json"), "source_audit_sha256": digest(evidence / "source-audit.json")}


def build_component(component, args, source, work, output, tools, deps):
    evidence = output / component
    evidence.mkdir()
    if component == "onboarding":
        python = tools["onboarding"]["python"]
        base = tools["onboarding"]["probe"]["base_prefix"]
        run([python, source / "scripts/build/build_native_onboarding.py", "--compiler-python", python,
             "--target", TARGETS[args.target][2], "--source-root", source / "ai-vision/agent-plugins/scripts",
             "--work-dir", work / "onboarding", "--output-dir", evidence,
             "--dependency-root", base + "/lib=CPython 3.11.14 upstream standard library",
             "--dependency-root", "/usr/lib=Debian Bookworm upstream native libraries",
             "--dependency-root", "/lib=Debian Bookworm upstream compatibility libraries"], log=work / "onboarding.log")
        info_path = evidence / TARGETS[args.target][2] / "build-info.json"
        info = json.loads(info_path.read_text())
        archive = evidence / TARGETS[args.target][2] / "connect.tar.gz"
        if digest(archive) != info["sha256"]:
            raise ValueError("installer archive differs from successful producer receipt")
        return {"archive": archive.relative_to(output).as_posix(), "sha256": info["sha256"],
                "build_info_sha256": digest(info_path), "smoke": ["--list-agents", "--hook-runtime --help"]}
    python = tools["service"]["python"]
    dependency = deps[component]
    runtime = evidence / "runtime"
    if component == "backend":
        run([python, source / "ai-vision/aivision-py-be/scripts/native_build.py", "--source", source / "ai-vision/aivision-py-be",
             "--bootstrap-source", source / "deploy/bootstrap", "--output", runtime,
             "--build-root", work / "backend", "--jobs", args.jobs,
             "--python-config", Path(sys.executable).resolve().with_name("python3.11-config")], log=work / "backend.log")
        copy_dependencies(dependency["venv"], runtime / ".venv", "backend")
        # Compiler evidence is not part of the image-facing runtime tree.
        shutil.move(runtime / "native-backend-manifest.json", evidence / "native-backend-manifest.json")
        run([runtime / "manage", "check"], cwd=runtime,
            env={"AIVISION_ENV": "container", "AIVISION_LOG_DIR": str(work / "smoke-logs")}, log=work / "backend-smoke.log")
        # Bootstrap helpers do not implement --help: calling one executes its
        # operation. Import only the backend's actual helpers, never a database
        # initializer. The database-init job runs in the AgentLens environment.
        run([runtime / ".venv/bin/python", "-c", "import backend_bootstrap, wait_http"],
            cwd=runtime, log=work / "backend-bootstrap-smoke.log")
        smoke = ["manage check", "import backend_bootstrap, wait_http"]
    else:
        run([python, source / "agentlens/native/build.py", "--source", source / "agentlens",
             "--output", evidence, "--dependency-venv", dependency["venv"], "--dependency-lock", dependency["lock"],
             "--database-init-source", source / "deploy/bootstrap/database-init.py",
             "--build-directory", work / "agentlens", "--jobs", args.jobs], log=work / "agentlens.log")
        # Apply the shared stricter editable/standalone-package checks to the final copy.
        # AgentLens producer already removes its owned package from every copied tree.
        for site in (runtime / ".venv").rglob("site-packages"):
            if any(site.glob("__editable__*")):
                raise ValueError("AgentLens dependency runtime contains an unreviewed editable install")
        run([runtime / "agentlens-migrate", "-c", runtime / "obschema/alembic.ini", "heads"],
            cwd=runtime, log=work / "agentlens-migrations-smoke.log")
        run([runtime / "src/agentlens-rule-worker", "--check-isolation"], cwd=runtime, log=work / "agentlens-isolation-smoke.log")
        run([runtime / ".venv/bin/python", "-c", "import agentlens.config, agentlens.main"], cwd=runtime,
            env={"PYTHONPATH": str(runtime / "src")}, log=work / "agentlens-import-smoke.log")
        smoke = ["agentlens-migrate heads", "agentlens-rule-worker --check-isolation", "import agentlens.config, agentlens.main"]
    audit = audit_runtime(source, runtime, evidence, args.target)
    return {"runtime": runtime.relative_to(output).as_posix(), **audit, "smoke": smoke}


def build(args):
    source, work, output = validate_paths(args)
    args.source_root = source
    components = COMPONENTS if args.component == "all" else (args.component,)
    check_runtime(probe_python(sys.executable), args.target, "build interpreter")
    for executable in ("gcc", "readelf"):
        if not shutil.which(executable):
            raise ValueError("compiler environment requires " + executable)
    initial = snapshot_inputs(source, components)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    tools = prepare_tools(args, work, components)
    dependencies = prepare_dependencies(args, source, work, tools, components)
    results = {}
    for component in components:
        print("Building " + component + " for " + args.target, flush=True)
        results[component] = build_component(component, args, source, work, output, tools, dependencies)
    if snapshot_inputs(source, components) != initial:
        raise ValueError("build inputs changed; discard partial outputs and use a frozen source snapshot")
    for component, dependency in dependencies.items():
        if (digest(dependency["lock"]) != dependency["lock_sha256"]
                or tree_identity(dependency["venv"]) != dependency["files_sha256"]):
            raise ValueError(component + " prepared dependency bytes changed during build")
    try:
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        revision = None
    receipt = {"schema_version": 1, "target": args.target, "source_revision": revision,
               "input_sha256": initial, "tools": tools, "dependencies": dependencies,
               "components": results, "inputs_stable": True,
               "qualification": "build/content/import checks only; database, diagnosis, OCI layers and deployment require separate acceptance"}
    pending = output / ".receipt.json.pending"
    pending.write_text(json.dumps(receipt, indent=2) + "\n")
    pending.replace(output / "receipt.json")
    return receipt


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--component", choices=(*COMPONENTS, "all"), default="all")
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tool-python", type=Path)
    parser.add_argument("--onboarding-tool-python", type=Path)
    for component in ("backend", "agentlens"):
        parser.add_argument("--" + component + "-deps-venv", type=Path)
        parser.add_argument("--" + component + "-deps-lock", type=Path)
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=1)
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        result = build(parse_args())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
    print(json.dumps({"target": result["target"], "components": list(result["components"]), "inputs_stable": True}))

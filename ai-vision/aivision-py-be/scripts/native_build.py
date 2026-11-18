"""Compile own backend code; supply the locked dependency venv separately."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import sysconfig

RUNTIME_DATA_FILES = (
    "apps/core/data/models-dev-api.zip",
    "apps/core/data/models-dev-LICENSE",
    "apps/core/data/models-dev-NOTICE.txt",
    "apps/core/trace_query/langfuse/native_v1_contract.json",
    "apps/core/fixtures/onboarding_demo.otlp",
    "docker-entrypoint.sh",
    ".env.container",
)


def input_identity(source, bootstrap):
    paths = {
        "backend/" + path.relative_to(source).as_posix(): path
        for package in ("apps", "aivision_backend", "config")
        for path in (source / package).rglob("*.py")
        if "tests" not in path.relative_to(source).parts and "__pycache__" not in path.parts
    }
    for relative in (
        "manage.py", "docker/manage.c",
        "scripts/native_build.py", "scripts/build_native.sh", "pyproject.toml", "uv.lock",
        *RUNTIME_DATA_FILES,
    ):
        paths["backend/" + relative] = source / relative
    for path in (source / "apps/core/templates").rglob("*"):
        if path.is_file():
            paths["backend/" + path.relative_to(source).as_posix()] = path
    build_info = source / "aivision_backend/build-info.json"
    if build_info.is_file():
        paths["backend/aivision_backend/build-info.json"] = build_info
    for name in ("backend-bootstrap.py", "database-init.py", "wait-http.py"):
        paths["bootstrap/" + name] = bootstrap / name
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(paths.items())}


def overlaps(first, second):
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def copy_runtime_data(source, output):
    shutil.copytree(source / "apps/core/templates", output / "apps/core/templates", dirs_exist_ok=True)
    for relative in RUNTIME_DATA_FILES:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    if (source / "aivision_backend/build-info.json").is_file():
        shutil.copy2(source / "aivision_backend/build-info.json", output / "aivision_backend/build-info.json")


def main():
    import Cython
    from Cython.Build import cythonize
    from setuptools import Extension, setup

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--bootstrap-source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--build-root", required=True, type=Path)
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=1)
    parser.add_argument("--python-config", default="python3-config")
    args = parser.parse_args()
    if Cython.__version__ != "3.2.4" or sys.version_info[:3] != (3, 11, 14):
        raise RuntimeError("Use target CPython 3.11.14 and Cython 3.2.4")
    if sys.platform != "linux":
        raise RuntimeError("The native CPython launcher currently targets Linux")
    source, bootstrap = args.source.resolve(), args.bootstrap_source.resolve()
    output, build = args.output.resolve(), args.build_root.resolve()
    if overlaps(build, output):
        raise ValueError("Generated build sources must stay outside the runtime artifact")
    if any(overlaps(root, directory) for root in (source, bootstrap) for directory in (build, output)):
        raise ValueError("Build/output directories must not overlap compiler input directories")
    identity = input_identity(source, bootstrap)
    output.mkdir(parents=True, exist_ok=True)
    if any(path.name != ".venv" for path in output.iterdir()):
        raise ValueError("Use empty runtime staging; an upstream .venv is allowed")
    work = build / "source"
    work.mkdir(parents=True, exist_ok=True)
    modules, aliases, sources = [], [], {}
    compiler_args = ["-O0", "-g0"]

    def add_module(path, relative):
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        original = ".".join(relative.with_suffix("").parts)
        name, inputs = original, [str(target)]
        if not relative.stem.isidentifier():
            # Cython requires identifier names, while CPython/Django permit
            # numeric migration names. Export the canonical CPython entry point.
            package, leaf = original.rsplit(".", 1)
            safe_leaf = "_" + leaf
            name = package + "." + safe_leaf
            safe = target.with_name("_" + target.name)
            shutil.copy2(target, safe)
            alias = target.with_suffix(".alias.c")
            alias.write_text(
                "#include <Python.h>\n"
                f"PyMODINIT_FUNC PyInit_{safe_leaf}(void);\n"
                f"PyMODINIT_FUNC PyInit_{leaf}(void) {{ return PyInit_{safe_leaf}(); }}\n"
            )
            inputs, aliases_entry = [str(safe), str(alias)], (name, original)
            aliases.append(aliases_entry)
        modules.append(Extension(name, inputs, extra_compile_args=compiler_args))
        sources[original] = hashlib.sha256(path.read_bytes()).hexdigest()

    for package in ("apps", "aivision_backend", "config"):
        for path in sorted((source / package).rglob("*.py")):
            relative = path.relative_to(source)
            if "tests" not in relative.parts and "__pycache__" not in relative.parts:
                add_module(path, relative)
    add_module(source / "manage.py", Path("manage.py"))
    commands = {"manage": ("manage", "main")}
    for command in ("backend-bootstrap", "database-init", "wait-http"):
        name = command.replace("-", "_")
        add_module(bootstrap / (command + ".py"), Path(name + ".py"))
        commands[command] = (name, "cli_main" if command == "database-init" else "main")
    setup(
        name="aivision-native-backend",
        ext_modules=cythonize(
            modules, build_dir=str(build / "generated"), nthreads=args.jobs,
            compiler_directives={"language_level": 3, "annotation_typing": False, "binding": True},
        ),
        script_args=["build_ext", "--build-lib", str(output), "--build-temp", str(build / "objects"), "--parallel", str(args.jobs)],
    )
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    for safe, original in aliases:
        parent, leaf = safe.rsplit(".", 1)
        directory = output.joinpath(*parent.split("."))
        (directory / (leaf + suffix)).rename(directory / (original.rsplit(".", 1)[1] + suffix))

    includes = shlex.split(subprocess.check_output([args.python_config, "--includes"], text=True))
    libraries = shlex.split(subprocess.check_output([args.python_config, "--embed", "--ldflags"], text=True))
    for command, (name, entry_function) in commands.items():
        subprocess.run([
            *shlex.split(os.environ.get("CC", "cc")), "-O2", "-Wall", "-Wextra", *includes,
            str(source / "docker/manage.c"), f'-DAIVISION_ENTRY_MODULE="{name}"',
            f'-DAIVISION_ENTRY_FUNCTION="{entry_function}"',
            "-o", str(output / command), *libraries, "-Wl,-rpath,/usr/local/lib",
        ], check=True)
    copy_runtime_data(source, output)
    if input_identity(source, bootstrap) != identity:
        raise RuntimeError("Compiler inputs changed during the build; use a frozen source snapshot")
    (output / "native-backend-manifest.json").write_text(json.dumps({
        "compiler": "Cython 3.2.4", "python": platform.python_version(),
        "architecture": platform.machine(), "soabi": sysconfig.get_config_var("SOABI"),
        "compile_args": compiler_args, "source_sha256": sources,
        "migration_aliases": aliases,
        "input_sha256": identity,
        "entrypoints": commands,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()

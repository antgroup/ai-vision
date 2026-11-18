#!/usr/bin/env python3
"""Build source-free AgentLens artifacts with the pinned Linux Python toolchain."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

COMPILER_VERSION = "3.2.4"
PYTHON_VERSION = (3, 11, 14)


def _overlap(first: Path, second: Path) -> bool:
    first, second = first.resolve(), second.resolve()
    return first == second or first in second.parents or second in first.parents


def source_identity(source: Path, database_init_source: Path | None = None) -> dict[str, str]:
    inputs = [*(source / "src").rglob("*.py"), *(source / "obschema").rglob("*.py")]
    inputs += list((source / "native").glob("*.c"))
    inputs += list((source / "native").glob("*.py"))
    inputs += [source / "native/requirements-build.txt"]
    inputs += [source / "docker/selfhost-entrypoint.sh", source / "obschema/alembic.ini"]
    inputs += [source / "env/env.dev", source / "env/env.prod"]
    inputs += [source / "uv.lock", source / "pyproject.toml"]
    identity = {
        path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(inputs)
    }
    if database_init_source is not None:
        identity["deploy/bootstrap/database-init.py"] = hashlib.sha256(
            database_init_source.read_bytes()
        ).hexdigest()
    return identity


def copy_dependencies(source: Path, target: Path) -> None:
    """Materialize the upstream environment and remove the owned project install."""
    if _overlap(source, target):
        raise ValueError("dependency source and runtime copy overlap")

    def ignore_duplicate_lib64(directory: str, names: list[str]) -> tuple[str, ...]:
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

    shutil.copytree(
        source, target, symlinks=False, ignore=ignore_duplicate_lib64, dirs_exist_ok=True
    )
    # Remove the owned installation from every copied tree, including wheel installs.
    for site in target.rglob("site-packages"):
        if not site.is_dir():
            continue
        for path in site.glob("*agentlens*.pth"):
            path.unlink()
        for path in site.glob("agentlens-*.dist-info"):
            shutil.rmtree(path)
        for directory in (site, site / "__pycache__"):
            for pattern in (
                "__editable___agentlens_*",
                "__editable__.agentlens-*",
                "_editable_impl_agentlens*",
            ):
                for path in directory.glob(pattern):
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
        shutil.rmtree(site / "agentlens", ignore_errors=True)


def build_native(
    source: Path,
    output: Path,
    *,
    dependency_venv: Path,
    dependency_lock: Path,
    database_init_source: Path | None = None,
    build_directory: Path | None = None,
    replace_runtime: bool = False,
    jobs: int = 1,
) -> Path:
    source, output = source.resolve(), output.resolve()
    if dependency_lock.read_bytes() != (source / "uv.lock").read_bytes():
        raise ValueError("dependency lock does not match AgentLens uv.lock")
    if output == source or source in output.parents:
        raise ValueError("output must be outside the AgentLens source directory")
    runtime = output / "runtime"
    if runtime.is_symlink():
        raise ValueError("runtime stage must not be a symlink")
    if runtime == source or runtime in source.parents:
        raise ValueError("runtime stage must not contain the source directory")
    if _overlap(dependency_venv, runtime):
        raise ValueError("dependency environment and runtime stage must not overlap")
    if not dependency_venv.is_dir():
        raise ValueError("dependency environment must be an existing directory")
    database_init_source = (
        database_init_source or source.parent / "deploy/bootstrap/database-init.py"
    ).resolve()
    if not database_init_source.is_file():
        raise ValueError("canonical database-init source is required: --database-init-source")
    if runtime in database_init_source.parents:
        raise ValueError("database-init source must be outside runtime stage")
    build = (build_directory or output / "build").resolve()
    if any(
        _overlap(build, protected)
        for protected in (source, runtime, dependency_venv, database_init_source)
    ):
        raise ValueError("build directory must be outside source, runtime and dependency inputs")
    if jobs not in {1, 2}:
        raise ValueError("native compilation supports one or two compiler jobs")
    if sys.platform != "linux" or sys.version_info[:3] != PYTHON_VERSION:
        raise ValueError("native packaging requires Linux CPython 3.11.14")
    if importlib.metadata.version("Cython") != COMPILER_VERSION:
        raise ValueError(f"native packaging requires Cython {COMPILER_VERSION}")
    from Cython.Build import cythonize
    from setuptools import Distribution, Extension

    if runtime.exists() and not replace_runtime:
        raise ValueError("runtime already exists; select a fresh output or --replace-runtime")
    if runtime.exists():
        # Keep the directory inode so an existing read-only validation mount sees
        # the refreshed stage. Only this explicit generated stage is replaced.
        for path in runtime.iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "src").mkdir()
    build = build / f"cython-{COMPILER_VERSION}-{os.uname().machine}"
    build.mkdir(parents=True, exist_ok=True)
    compiler = Path(sys.executable).resolve()
    identity = source_identity(source, database_init_source)
    extensions = []
    placements = []

    def add_module(name: str, path: Path, destination: Path) -> None:
        extensions.append(Extension(name, [str(path)], extra_compile_args=["-O0", "-g0"]))
        placements.append((name, destination))

    for path in sorted((source / "src").rglob("*.py")):
        relative = path.relative_to(source / "src").with_suffix("")
        # Keep package.__init__: CPython then recognizes __init__.so as a package.
        add_module(".".join(relative.parts), path, runtime / "src" / relative.parent)
    for name, original in [
        (
            "agentlens_rule_worker_impl",
            source / "src/agentlens/diagnoser/executors/rule/custom/worker_main.py",
        ),
        ("agentlens_migrations", source / "native/migrations.py"),
        ("agentlens_database_init", database_init_source),
    ]:
        staging = build / "entry-inputs"
        staging.mkdir(exist_ok=True)
        staged = staging / f"{name}.py"
        # Cython cannot compile the same source path under two module names in
        # one batch. Only private build inputs are duplicated, never runtime source.
        shutil.copy2(original, staged)
        add_module(name, staged, runtime / "src")
    for original in sorted((source / "obschema/migrations").rglob("*.py")):
        name = "env" if original.name == "env.py" else f"revision_{original.stem}"
        destination = runtime / "obschema" / original.relative_to(source / "obschema").parent
        add_module(name, original, destination)
    library = build / "lib"
    if library.is_symlink():
        raise ValueError("compiled library directory must not be a symlink")
    if library.exists():
        shutil.rmtree(library)
    modules = cythonize(
        extensions,
        build_dir=str(build / "generated"),
        nthreads=jobs,
        force=True,
        compiler_directives={"language_level": 3, "binding": True, "annotation_typing": False},
    )
    distribution = Distribution({"name": "agentlens-native", "ext_modules": modules})
    command = distribution.get_command_obj("build_ext")
    command.build_lib = str(library)
    command.build_temp = str(build / "temp")
    command.parallel = jobs
    distribution.run_command("build_ext")
    for name, destination in placements:
        parts = name.split(".")
        compiled = library.joinpath(*parts[:-1], parts[-1] + sysconfig.get_config_var("EXT_SUFFIX"))
        if not compiled.is_file():
            raise ValueError(f"expected one compiled extension for {name}")
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(compiled, destination / compiled.name)
    (runtime / "env").mkdir()
    for name in ("env.dev", "env.prod"):
        shutil.copy2(source / "env" / name, runtime / "env" / name)
    shutil.copy2(source / "obschema/alembic.ini", runtime / "obschema/alembic.ini")
    shutil.copy2(source / "docker/selfhost-entrypoint.sh", runtime / "agentlens-entrypoint")
    (runtime / "agentlens-entrypoint").chmod(0o755)
    flags = shlex.split(
        subprocess.check_output(
            [
                str(compiler.with_name("python3.11-config")),
                "--embed",
                "--cflags",
                "--ldflags",
            ],
            text=True,
        )
    )
    for original, destination in [
        ("custom_rule_worker.c", runtime / "src/agentlens-rule-worker"),
        ("migrations.c", runtime / "agentlens-migrate"),
    ]:
        subprocess.run(
            ["gcc", str(source / "native" / original), "-o", str(destination), *flags], check=True
        )
    subprocess.run(
        [
            "gcc",
            str(source / "native/migrations.c"),
            '-DAGENTLENS_ENTRY_MODULE="agentlens_database_init"',
            '-DAGENTLENS_ENTRY_FUNCTION="cli_main"',
            "-o",
            str(runtime / "database-init"),
            *flags,
        ],
        check=True,
    )
    copy_dependencies(dependency_venv, runtime / ".venv")
    if source_identity(source, database_init_source) != identity:
        raise ValueError("source changed during compilation; rebuild from fixed inputs")
    if hashlib.sha256(dependency_lock.read_bytes()).hexdigest() != identity["uv.lock"]:
        raise ValueError("dependency lock changed during compilation")
    if any(
        path.suffix in {".py", ".pyc", ".pyo", ".pyi", ".c", ".pyx"}
        for path in runtime.rglob("*")
        if ".venv" not in path.relative_to(runtime).parts
    ):
        raise ValueError("self-authored source or build output found in runtime stage")
    (output / "input-hashes.json").write_text(json.dumps(identity, indent=2) + "\n")
    (output / "toolchain.json").write_text(
        json.dumps(
            {
                "compiler": f"Cython=={COMPILER_VERSION}",
                "directives": {"language_level": 3, "binding": True, "annotation_typing": False},
                "extension_cflags": ["-O0", "-g0"],
                "python": sys.version,
                "platform": sys.platform,
                "architecture": os.uname().machine,
                "gcc": subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0],
                "dependency_lock_sha256": hashlib.sha256(dependency_lock.read_bytes()).hexdigest(),
                "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    return runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dependency-venv", type=Path, required=True)
    parser.add_argument("--dependency-lock", type=Path, required=True)
    parser.add_argument("--database-init-source", type=Path)
    parser.add_argument("--build-directory", type=Path)
    parser.add_argument("--replace-runtime", action="store_true")
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    print(
        build_native(
            args.source,
            args.output,
            dependency_venv=args.dependency_venv,
            dependency_lock=args.dependency_lock,
            database_init_source=args.database_init_source,
            build_directory=args.build_directory,
            replace_runtime=args.replace_runtime,
            jobs=args.jobs,
        )
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Compile the isolated Rule worker for the optional source distribution image.

Private binary delivery continues to use deploy/native-packaging/build.py.
This helper deliberately builds only the worker, not a private product runtime.
"""

import argparse
import importlib.metadata
import shlex
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path


def build(source: Path, output: Path, *, enhanced: bool = False) -> None:
    if sys.platform != "linux" or sys.version_info[:3] != (3, 11, 14):
        raise ValueError("the worker requires Linux CPython 3.11.14")
    if importlib.metadata.version("Cython") != "3.2.4":
        raise ValueError("the worker requires Cython 3.2.4")
    from Cython.Build import cythonize
    from setuptools import Distribution, Extension

    output.mkdir(parents=True, exist_ok=False)
    inputs = output.parent / "worker-build"
    inputs.mkdir(exist_ok=False)
    implementation = inputs / "agentlens_rule_worker_impl.py"
    shutil.copy2(
        source / "src/agentlens/diagnoser/executors/rule/custom/worker_main.py", implementation
    )
    extensions = [
        Extension(
            "agentlens_rule_worker_impl",
            [str(implementation)],
            extra_compile_args=["-O0", "-g0"],
        )
    ]
    if enhanced:
        for name in ("enhanced_contract", "enhanced_context", "enhanced_worker"):
            module = inputs / (name + ".py")
            shutil.copy2(
                source / "src/agentlens/diagnoser/executors/rule/custom" / (name + ".py"),
                module,
            )
            extensions.append(Extension(name, [str(module)], extra_compile_args=["-O0", "-g0"]))
    modules = cythonize(
        extensions,
        build_dir=str(inputs / "generated"),
        compiler_directives={"language_level": 3, "binding": True, "annotation_typing": False},
    )
    distribution = Distribution({"name": "agentlens-sandbox-worker", "ext_modules": modules})
    command = distribution.get_command_obj("build_ext")
    command.build_lib = str(output)
    command.build_temp = str(inputs / "objects")
    distribution.run_command("build_ext")
    expected = output / ("agentlens_rule_worker_impl" + sysconfig.get_config_var("EXT_SUFFIX"))
    if not expected.is_file():
        raise ValueError("compiled worker module is missing")
    flags = shlex.split(
        subprocess.check_output(
            [
                str(Path(sys.executable).resolve().with_name("python3.11-config")),
                "--embed",
                "--cflags",
                "--ldflags",
            ],
            text=True,
        )
    )
    executable = output / "agentlens-rule-worker"
    subprocess.run(
        [
            "gcc",
            "-DAGENTLENS_SANDBOX_WORKER",
            str(source / "native/custom_rule_worker.c"),
            "-o",
            str(executable),
            *flags,
        ],
        check=True,
    )
    subprocess.run([str(executable), "--check-isolation"], check=True)
    if enhanced:
        runtime = output / "rule-runtime"
        runtime.mkdir()
        for name in ("enhanced_contract", "enhanced_context", "enhanced_worker"):
            module = output / (name + sysconfig.get_config_var("EXT_SUFFIX"))
            if not module.is_file():
                raise ValueError("compiled enhanced runtime module is missing")
            shutil.move(module, runtime / module.name)
        subprocess.run(
            [
                "gcc",
                str(source / "native/enhanced_rule_worker.c"),
                "-o",
                str(runtime / "enhanced-worker"),
                *flags,
            ],
            check=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--enhanced", action="store_true")
    arguments = parser.parse_args()
    build(arguments.source.resolve(), arguments.output.resolve(), enhanced=arguments.enhanced)

#!/usr/bin/env python3
"""Atomic exact-core cohort staging for Hermes production and replay products."""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

PRODUCTS = {"production", "replay"}
CORE_DISTRIBUTION = "aivision-hermes-core"
STATE_FILE = "cohort.json"


class CohortError(RuntimeError):
    pass


@dataclass(frozen=True)
class Artifact:
    root: Path
    distribution: str
    version: str
    required_core: str
    dependencies: tuple[str, ...]

    @classmethod
    def load(cls, root: str | Path, *, core: bool = False) -> "Artifact":
        project_root = Path(root).expanduser().resolve()
        path = project_root / "pyproject.toml"
        try:
            metadata = tomllib.loads(path.read_text(encoding="utf-8"))
            project = metadata["project"]
            distribution = str(project["name"])
            version = str(project["version"])
            dependencies = tuple(
                str(value) for value in project.get("dependencies", [])
            )
        except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
            raise CohortError(f"cannot read artifact metadata at {path}") from error
        if not distribution or not version:
            raise CohortError(f"artifact metadata is incomplete at {path}")
        if core:
            if distribution != CORE_DISTRIBUTION:
                raise CohortError(f"core artifact must be {CORE_DISTRIBUTION}")
            required_core = version
        else:
            exact = [
                dependency.split("==", 1)[1]
                for dependency in dependencies
                if dependency.startswith(f"{CORE_DISTRIBUTION}==")
                and dependency.count("==") == 1
            ]
            if len(exact) != 1 or any(
                dependency.startswith(CORE_DISTRIBUTION) and "==" not in dependency
                for dependency in dependencies
            ):
                raise CohortError(
                    f"{distribution} must declare one exact {CORE_DISTRIBUTION} "
                    "requirement"
                )
            required_core = exact[0]
        return cls(project_root, distribution, version, required_core, dependencies)


def _copytree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "dist", "build"),
    )


def _private_json(path: Path, value: dict[str, Any]) -> None:
    content = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


class CohortManager:
    """Manage a complete staged cohort as one rename-based transaction."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.current = self.root / "current"
        self.previous = self.root / "previous"

    def state(self, directory: Path | None = None) -> dict[str, Any]:
        base = self.current if directory is None else directory
        path = base / STATE_FILE
        if not path.is_file():
            return {"version": 1, "core": None, "products": {}}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CohortError(f"invalid cohort state at {path}") from error
        if not isinstance(value, dict) or not isinstance(value.get("products"), dict):
            raise CohortError(f"invalid cohort state at {path}")
        return value

    @staticmethod
    def _assert_product(product: str) -> None:
        if product not in PRODUCTS:
            raise CohortError(f"product must be one of: {', '.join(sorted(PRODUCTS))}")

    @staticmethod
    def _validate_compatibility(state: dict[str, Any]) -> None:
        products = state.get("products", {})
        requirements = {
            str(metadata.get("requiredCore", ""))
            for metadata in products.values()
            if isinstance(metadata, dict)
        }
        if len(requirements) > 1:
            raise CohortError("installed products require incompatible core versions")
        core = state.get("core")
        if products and not isinstance(core, dict):
            raise CohortError("installed products require a core artifact")
        if requirements:
            if not isinstance(core, dict):
                raise CohortError("installed products require a core artifact")
            if str(core.get("version", "")) not in requirements:
                raise CohortError("installed core does not satisfy all products")
        if not products and core is not None:
            raise CohortError("orphaned core is not a valid cohort")

    def _stage_current(self) -> tuple[Path, Path]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = Path(tempfile.mkdtemp(prefix=".cohort-stage-", dir=self.root))
        stage = temporary / "current"
        if self.current.is_dir():
            _copytree(self.current, stage)
        else:
            stage.mkdir(mode=0o700)
        return temporary, stage

    @staticmethod
    def _validate_sources(stage: Path, state: dict[str, Any]) -> None:
        CohortManager._validate_compatibility(state)
        for name in ("core", *sorted(PRODUCTS)):
            path = stage / name
            expected = (
                state.get("core") if name == "core" else state["products"].get(name)
            )
            if expected is None:
                if path.exists():
                    raise CohortError(f"unexpected staged artifact: {name}")
                continue
            if not path.is_dir() or not (path / "pyproject.toml").is_file():
                raise CohortError(f"staged artifact is incomplete: {name}")
            for source in path.rglob("*.py"):
                try:
                    ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
                except (OSError, SyntaxError, UnicodeDecodeError) as error:
                    raise CohortError(
                        f"staged artifact does not compile: {source}"
                    ) from error

    def _commit(self, stage: Path, *, fail_phase: str = "") -> None:
        if fail_phase == "before-swap":
            raise CohortError("simulated cohort failure before swap")
        old_previous = self.root / ".previous-displaced"
        if old_previous.exists():
            shutil.rmtree(old_previous)
        current_displaced = False
        previous_displaced = False
        promoted = False
        try:
            if self.previous.exists():
                os.replace(self.previous, old_previous)
                previous_displaced = True
            if self.current.exists():
                os.replace(self.current, self.previous)
                current_displaced = True
            if fail_phase == "during-swap":
                raise CohortError("simulated cohort failure during swap")
            os.replace(stage, self.current)
            promoted = True
            if fail_phase == "after-swap":
                raise CohortError("simulated cohort failure after swap")
        except Exception:
            if promoted and self.current.exists():
                shutil.rmtree(self.current)
            if current_displaced and self.previous.exists():
                os.replace(self.previous, self.current)
            if previous_displaced and old_previous.exists():
                os.replace(old_previous, self.previous)
            raise
        if old_previous.exists():
            shutil.rmtree(old_previous)

    def _install_into_stage(
        self,
        stage: Path,
        state: dict[str, Any],
        product: str,
        artifact: Artifact,
        core: Artifact,
    ) -> None:
        self._assert_product(product)
        if (
            core.distribution != CORE_DISTRIBUTION
            or core.version != artifact.required_core
        ):
            raise CohortError(
                f"{artifact.distribution} requires core {artifact.required_core}, "
                f"got {core.version}"
            )
        sibling = next((name for name in PRODUCTS if name != product), "")
        sibling_state = state["products"].get(sibling)
        if (
            sibling_state
            and sibling_state.get("requiredCore") != artifact.required_core
        ):
            raise CohortError(
                f"cannot change {product} alone: {sibling} requires core "
                f"{sibling_state.get('requiredCore')}"
            )
        for name, source in (("core", core.root), (product, artifact.root)):
            destination = stage / name
            if destination.exists():
                shutil.rmtree(destination)
            _copytree(source, destination)
        state["core"] = {"distribution": core.distribution, "version": core.version}
        state["products"][product] = {
            "distribution": artifact.distribution,
            "version": artifact.version,
            "requiredCore": artifact.required_core,
        }

    def install_product(
        self,
        product: str,
        artifact: Artifact,
        core: Artifact,
        *,
        fail_phase: str = "",
    ) -> None:
        current_state = self.state()
        self._assert_product(product)
        sibling = next((name for name in PRODUCTS if name != product), "")
        sibling_state = current_state["products"].get(sibling)
        if (
            sibling_state
            and sibling_state.get("requiredCore") != artifact.required_core
        ):
            raise CohortError(
                f"cannot change {product} alone: {sibling} requires core "
                f"{sibling_state.get('requiredCore')}"
            )
        temporary, stage = self._stage_current()
        try:
            staged_state = self.state(stage)
            self._install_into_stage(stage, staged_state, product, artifact, core)
            _private_json(stage / STATE_FILE, staged_state)
            self._validate_sources(stage, staged_state)
            self._commit(stage, fail_phase=fail_phase)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def install_cohort(
        self,
        production: Artifact,
        replay: Artifact,
        core: Artifact,
        *,
        fail_phase: str = "",
    ) -> None:
        if production.required_core != replay.required_core:
            raise CohortError(
                "production and replay artifacts require different core versions"
            )
        temporary, stage = self._stage_current()
        try:
            staged_state = {"version": 1, "core": None, "products": {}}
            for path in tuple(stage.iterdir()):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            self._install_into_stage(
                stage, staged_state, "production", production, core
            )
            self._install_into_stage(stage, staged_state, "replay", replay, core)
            _private_json(stage / STATE_FILE, staged_state)
            self._validate_sources(stage, staged_state)
            self._commit(stage, fail_phase=fail_phase)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def uninstall_product(self, product: str, *, fail_phase: str = "") -> None:
        self._assert_product(product)
        current_state = self.state()
        if product not in current_state["products"]:
            raise CohortError(f"{product} is not installed")
        temporary, stage = self._stage_current()
        try:
            staged_state = self.state(stage)
            staged_state["products"].pop(product)
            product_path = stage / product
            if product_path.exists():
                shutil.rmtree(product_path)
            if not staged_state["products"]:
                staged_state["core"] = None
                core_path = stage / "core"
                if core_path.exists():
                    shutil.rmtree(core_path)
            _private_json(stage / STATE_FILE, staged_state)
            self._validate_sources(stage, staged_state)
            self._commit(stage, fail_phase=fail_phase)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def rollback_cohort(self, *, fail_phase: str = "") -> None:
        if not self.previous.is_dir():
            raise CohortError("no previous cohort is available")
        previous_state = self.state(self.previous)
        self._validate_sources(self.previous, previous_state)
        temporary, stage = self._stage_current()
        try:
            shutil.rmtree(stage)
            _copytree(self.previous, stage)
            self._commit(stage, fail_phase=fail_phase)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def rollback_product(self, product: str, *, fail_phase: str = "") -> None:
        self._assert_product(product)
        if not self.previous.is_dir():
            raise CohortError("no previous cohort is available")
        current_state = self.state()
        previous_state = self.state(self.previous)
        candidate = previous_state["products"].get(product)
        sibling = next((name for name in PRODUCTS if name != product), "")
        sibling_state = current_state["products"].get(sibling)
        candidate_requirement = candidate.get("requiredCore") if candidate else None
        if (
            sibling_state
            and candidate_requirement
            and sibling_state.get("requiredCore") != candidate_requirement
        ):
            raise CohortError(
                f"cannot roll back {product} alone: {sibling} requires core "
                f"{sibling_state.get('requiredCore')}"
            )
        temporary, stage = self._stage_current()
        try:
            staged_state = self.state(stage)
            target = stage / product
            if target.exists():
                shutil.rmtree(target)
            if candidate:
                _copytree(self.previous / product, target)
                staged_state["products"][product] = candidate
            else:
                staged_state["products"].pop(product, None)
            requirements = {
                metadata["requiredCore"]
                for metadata in staged_state["products"].values()
            }
            if requirements:
                required = next(iter(requirements))
                previous_core = previous_state.get("core")
                if (
                    candidate
                    and previous_core
                    and previous_core.get("version") == required
                ):
                    core_target = stage / "core"
                    if core_target.exists():
                        shutil.rmtree(core_target)
                    _copytree(self.previous / "core", core_target)
                    staged_state["core"] = previous_core
            else:
                core_target = stage / "core"
                if core_target.exists():
                    shutil.rmtree(core_target)
                staged_state["core"] = None
            _private_json(stage / STATE_FILE, staged_state)
            self._validate_sources(stage, staged_state)
            self._commit(stage, fail_phase=fail_phase)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install-product")
    install.add_argument("--product", choices=sorted(PRODUCTS), required=True)
    install.add_argument("--artifact", required=True, type=Path)
    install.add_argument("--core", required=True, type=Path)
    cohort = subparsers.add_parser("install-cohort")
    cohort.add_argument("--production", required=True, type=Path)
    cohort.add_argument("--replay", required=True, type=Path)
    cohort.add_argument("--core", required=True, type=Path)
    uninstall = subparsers.add_parser("uninstall-product")
    uninstall.add_argument("--product", choices=sorted(PRODUCTS), required=True)
    rollback = subparsers.add_parser("rollback-product")
    rollback.add_argument("--product", choices=sorted(PRODUCTS), required=True)
    subparsers.add_parser("rollback-cohort")
    args = parser.parse_args(argv)
    manager = CohortManager(args.root)
    try:
        if args.command == "install-product":
            manager.install_product(
                args.product,
                Artifact.load(args.artifact),
                Artifact.load(args.core, core=True),
            )
        elif args.command == "install-cohort":
            manager.install_cohort(
                Artifact.load(args.production),
                Artifact.load(args.replay),
                Artifact.load(args.core, core=True),
            )
        elif args.command == "uninstall-product":
            manager.uninstall_product(args.product)
        elif args.command == "rollback-product":
            manager.rollback_product(args.product)
        else:
            manager.rollback_cohort()
    except (CohortError, OSError, shutil.Error) as error:
        print(
            f"Cohort operation failed: {type(error).__name__}: {error}", file=sys.stderr
        )
        return 1
    print(json.dumps(manager.state(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

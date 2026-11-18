#!/usr/bin/env python3
"""Compensating transaction coordinator for managed Hermes products."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HERMES_ROOT = Path(__file__).resolve().parents[1]
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

from scripts.hermes_cohort import (  # noqa: E402
    CORE_DISTRIBUTION,
    Artifact,
    CohortError,
    CohortManager,
)
from scripts.install_plugin import (  # noqa: E402
    backup_path,
    prepare_config_plan,
    transaction_path,
    undo_install,
    validate_package,
)
from scripts.install_plugin import (  # noqa: E402
    install as install_plugin,
)
from scripts.install_plugin import (  # noqa: E402
    rollback as rollback_plugin,
)


class LifecycleError(RuntimeError):
    pass


@dataclass(frozen=True)
class PathSnapshot:
    target: Path
    saved: Path | None
    was_directory: bool = False

    @classmethod
    def capture(cls, target: Path, root: Path, index: int) -> "PathSnapshot":
        target = target.expanduser().resolve()
        if not target.exists():
            return cls(target, None)
        saved = root / str(index)
        if target.is_dir():
            shutil.copytree(target, saved)
            return cls(target, saved, True)
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, saved)
        return cls(target, saved, False)

    def restore(self) -> None:
        if self.target.is_dir():
            shutil.rmtree(self.target)
        elif self.target.exists():
            self.target.unlink()
        if self.saved is None:
            return
        self.target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.was_directory:
            shutil.copytree(self.saved, self.target)
        else:
            shutil.copy2(self.saved, self.target)


class LifecycleTransaction:
    def __init__(
        self,
        *,
        python: Path,
        cohort_root: Path,
        plugin: Path,
        config: Path,
        legacy_env: Path,
    ) -> None:
        # Keep a virtualenv interpreter symlink intact; resolving it would escape
        # the environment and mutate the base interpreter instead.
        self.python = Path(os.path.abspath(python.expanduser()))
        self.cohort_root = cohort_root.expanduser().resolve()
        self.plugin = plugin.expanduser().resolve()
        self.config = config.expanduser().resolve()
        self.legacy_env = legacy_env.expanduser().resolve()
        self._temporary = tempfile.TemporaryDirectory(prefix="hermes-lifecycle-")
        root = Path(self._temporary.name)
        config_legacy_backup = (
            self.config.parent / "config-backups/observ-hermes.env.previous"
        )
        legacy_plugin_backup = self.plugin.with_name(f"{self.plugin.name}.previous")
        plugin_backup = backup_path(self.plugin)
        managed_paths = (
            self.cohort_root,
            self.plugin,
            plugin_backup,
            transaction_path(self.plugin),
            legacy_plugin_backup,
            plugin_backup.with_name(f"{self.plugin.name}.legacy-previous"),
            self.config,
            self.legacy_env,
            config_legacy_backup,
        )
        self.snapshots = [
            PathSnapshot.capture(path, root / "paths", index)
            for index, path in enumerate(managed_paths)
        ]
        cohort_snapshot = self.snapshots[0].saved
        self.previous_current = cohort_snapshot / "current" if cohort_snapshot else None
        self.previous_cohort = cohort_snapshot / "previous" if cohort_snapshot else None

    def close(self) -> None:
        self._temporary.cleanup()

    def restore_files(self) -> None:
        for snapshot in reversed(self.snapshots):
            snapshot.restore()

    def restore_distributions(self) -> None:
        self._reconcile_distributions(self.previous_current)

    def reconcile_current_distributions(self) -> None:
        current = self.cohort_root / "current"
        self._reconcile_distributions(current if current.is_dir() else None)

    def _pip(self, *arguments: str, quiet: bool = False) -> None:
        command = [str(self.python), "-m", "pip", *arguments]
        result = subprocess.run(command, text=True, capture_output=quiet)
        if result.returncode != 0:
            detail = result.stderr.strip() if quiet else ""
            raise LifecycleError(
                f"pip operation failed ({' '.join(arguments)}): "
                f"{detail or result.returncode}"
            )

    def _reconcile_distributions(self, current: Path | None) -> None:
        has_core = bool(current and (current / "core").is_dir())
        has_production = bool(current and (current / "production").is_dir())
        has_replay = bool(current and (current / "replay").is_dir())
        if has_core:
            assert current is not None
            self._pip(
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--upgrade",
                str(current / "core"),
            )
        else:
            self._pip("uninstall", "-y", "aivision-hermes-core", quiet=True)
        if has_production:
            assert current is not None
            production = Artifact.load(current / "production")
            runtime_dependencies = tuple(
                dependency
                for dependency in production.dependencies
                if not dependency.startswith(f"{CORE_DISTRIBUTION}==")
            )
            if runtime_dependencies:
                self._pip(
                    "install",
                    "--disable-pip-version-check",
                    "--upgrade",
                    *runtime_dependencies,
                )
        if has_replay:
            assert current is not None
            self._pip(
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--upgrade",
                str(current / "replay"),
            )
        else:
            self._pip("uninstall", "-y", "hermes-replay", quiet=True)


def _inject(phase: str) -> None:
    configured = os.environ.get("OBSERV_HERMES_LIFECYCLE_FAIL_PHASE", "")
    legacy = os.environ.get("OBSERV_HERMES_INSTALL_FAIL_PHASE", "")
    aliases = {
        "after-cohort": {"after-cohort", "cohort"},
        "after-distribution": {"after-distribution", "distribution", "pip"},
        "after-plugin": {"after-plugin", "plugin"},
    }
    if configured in aliases.get(phase, set()) or legacy in aliases.get(phase, set()):
        raise LifecycleError(f"simulated lifecycle failure at {phase}")


def _plugin_fail_phase() -> str:
    value = os.environ.get("OBSERV_HERMES_INSTALL_FAIL_PHASE", "")
    return value if value in {"copy", "validate", "config", "swap"} else ""


def _simulate_cohort(root: Path, operation: Callable[[CohortManager], None]) -> None:
    with tempfile.TemporaryDirectory(prefix="hermes-cohort-preflight-") as temporary:
        simulated = Path(temporary) / "cohort"
        if root.is_dir():
            shutil.copytree(root, simulated)
        operation(CohortManager(simulated))


def _seed_empty_current(manager: CohortManager) -> None:
    if not manager.current.exists():
        manager.current.mkdir(parents=True, mode=0o700)
        state = manager.current / "cohort.json"
        state.write_text(
            json.dumps({"version": 1, "core": None, "products": {}}, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        if os.name == "posix":
            os.chmod(state, 0o600)


def _preflight_python(python: Path) -> None:
    pip_probe = subprocess.run(
        [str(python), "-m", "pip", "--version"], text=True, capture_output=True
    )
    if pip_probe.returncode == 0:
        return

    bootstrap = subprocess.run(
        [str(python), "-m", "ensurepip", "--upgrade"],
        text=True,
        capture_output=True,
    )
    if bootstrap.returncode != 0:
        raise LifecycleError(
            f"Python environment cannot bootstrap pip with ensurepip: {python}"
        )

    pip_probe = subprocess.run(
        [str(python), "-m", "pip", "--version"], text=True, capture_output=True
    )
    if pip_probe.returncode != 0:
        raise LifecycleError(
            f"Python environment has no usable pip after ensurepip: {python}"
        )


def _run_transaction(
    args: argparse.Namespace,
    *,
    cohort_operation: Callable[[CohortManager], None],
    plugin_operation: Callable[[LifecycleTransaction], None],
) -> None:
    _preflight_python(args.python)
    _simulate_cohort(args.cohort_root, cohort_operation)
    transaction = LifecycleTransaction(
        python=args.python,
        cohort_root=args.cohort_root,
        plugin=args.plugin,
        config=args.config,
        legacy_env=args.legacy_env,
    )
    try:
        cohort_operation(CohortManager(args.cohort_root))
        _inject("after-cohort")
        transaction.reconcile_current_distributions()
        _inject("after-distribution")
        plugin_operation(transaction)
        _inject("after-plugin")
    except Exception as original:
        recovery_errors: list[str] = []
        try:
            transaction.restore_files()
        except Exception as error:  # recovery must attempt every managed surface
            recovery_errors.append(f"files: {type(error).__name__}: {error}")
        try:
            transaction.restore_distributions()
        except Exception as error:  # recovery must attempt every managed surface
            recovery_errors.append(f"distributions: {type(error).__name__}: {error}")
        if recovery_errors:
            raise LifecycleError(
                f"operation failed ({type(original).__name__}: {original}); "
                f"compensation also failed ({'; '.join(recovery_errors)})"
            ) from original
        raise
    finally:
        transaction.close()


def _install_plugin(
    args: argparse.Namespace, source: Path, *, with_config: bool
) -> None:
    if with_config:
        install_plugin(
            source,
            args.plugin,
            config_template=args.config_template,
            config_destination=args.config,
            legacy_env=args.legacy_env,
            fail_phase=_plugin_fail_phase(),
        )
    else:
        install_plugin(source, args.plugin, fail_phase=_plugin_fail_phase())


def _restore_production_source(
    args: argparse.Namespace,
    source: Path,
    *,
    undo_installer_transaction: bool,
) -> None:
    if undo_installer_transaction and backup_path(args.plugin).is_dir():
        rollback_plugin(args.plugin)
    _install_plugin(args, source, with_config=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--cohort-root", required=True, type=Path)
    parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--legacy-env", required=True, type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_product = subparsers.add_parser("install-product")
    install_product.add_argument(
        "--product", choices=("production", "replay"), required=True
    )
    install_product.add_argument("--artifact", required=True, type=Path)
    install_product.add_argument("--core", required=True, type=Path)
    install_product.add_argument("--config-template", type=Path)
    install_cohort = subparsers.add_parser("install-cohort")
    install_cohort.add_argument("--production", required=True, type=Path)
    install_cohort.add_argument("--replay", required=True, type=Path)
    install_cohort.add_argument("--core", required=True, type=Path)
    install_cohort.add_argument("--config-template", required=True, type=Path)
    uninstall = subparsers.add_parser("uninstall-product")
    uninstall.add_argument("--product", choices=("production", "replay"), required=True)
    rollback_product = subparsers.add_parser("rollback-product")
    rollback_product.add_argument(
        "--product", choices=("production", "replay"), required=True
    )
    subparsers.add_parser("rollback-cohort")
    args = parser.parse_args(argv)

    try:
        if args.command == "install-product":
            artifact = Artifact.load(args.artifact)
            core = Artifact.load(args.core, core=True)
            if args.product == "production":
                if args.config_template is None:
                    raise LifecycleError(
                        "production install requires --config-template"
                    )
                validate_package(artifact.root)
                prepare_config_plan(
                    template=args.config_template,
                    destination=args.config,
                    legacy_env=args.legacy_env,
                )

            def operation(manager: CohortManager) -> None:
                _seed_empty_current(manager)
                manager.install_product(args.product, artifact, core)

            if args.product == "production":

                def plugin_operation(_transaction: LifecycleTransaction) -> None:
                    _install_plugin(args, artifact.root, with_config=True)

            else:

                def plugin_operation(_transaction: LifecycleTransaction) -> None:
                    return None
        elif args.command == "install-cohort":
            production = Artifact.load(args.production)
            replay = Artifact.load(args.replay)
            core = Artifact.load(args.core, core=True)
            validate_package(production.root)
            prepare_config_plan(
                template=args.config_template,
                destination=args.config,
                legacy_env=args.legacy_env,
            )

            def operation(manager: CohortManager) -> None:
                _seed_empty_current(manager)
                manager.install_cohort(production, replay, core)

            def plugin_operation(_transaction: LifecycleTransaction) -> None:
                _install_plugin(args, production.root, with_config=True)

        elif args.command == "uninstall-product":
            if args.product == "production" and not args.plugin.is_dir():
                raise LifecycleError(
                    f"production plugin is not installed: {args.plugin}"
                )

            def operation(manager: CohortManager) -> None:
                manager.uninstall_product(args.product)

            if args.product == "production":

                def plugin_operation(_transaction: LifecycleTransaction) -> None:
                    shutil.rmtree(args.plugin)

            else:

                def plugin_operation(_transaction: LifecycleTransaction) -> None:
                    return None
        elif args.command == "rollback-product":
            manager = CohortManager(args.cohort_root)
            current_state = manager.state()
            previous_state = (
                manager.state(manager.previous) if manager.previous.is_dir() else None
            )
            if previous_state is None:
                raise CohortError("no previous cohort is available")

            def operation(manager: CohortManager) -> None:
                manager.rollback_product(args.product)

            if args.product != "production":

                def plugin_operation(_transaction: LifecycleTransaction) -> None:
                    return None

            elif "production" in previous_state["products"]:
                source = manager.previous / "production"
                validate_package(source)
                undo = "production" in current_state["products"]
                if undo and backup_path(args.plugin).is_dir():
                    rollback_plugin(args.plugin, dry_run=True)

                def restore_product_plugin(
                    _transaction: LifecycleTransaction,
                ) -> None:
                    assert _transaction.previous_cohort is not None
                    _restore_production_source(
                        args,
                        _transaction.previous_cohort / "production",
                        undo_installer_transaction=undo,
                    )

                plugin_operation = restore_product_plugin

            elif backup_path(args.plugin).is_dir():
                rollback_plugin(args.plugin, dry_run=True)

                def plugin_operation(_transaction: LifecycleTransaction) -> None:
                    rollback_plugin(args.plugin)

            elif transaction_path(args.plugin).is_file():
                undo_install(args.plugin, dry_run=True)

                def plugin_operation(_transaction: LifecycleTransaction) -> None:
                    undo_install(args.plugin)

            else:

                def plugin_operation(_transaction: LifecycleTransaction) -> None:
                    if args.plugin.exists():
                        shutil.rmtree(args.plugin)

        else:
            simulated = CohortManager(args.cohort_root)
            if simulated.previous.is_dir():
                desired = simulated.state(simulated.previous)
            else:
                raise CohortError("no previous cohort is available")

            def operation(manager: CohortManager) -> None:
                manager.rollback_cohort()

            if "production" in desired["products"]:
                source = simulated.previous / "production"
                validate_package(source)
                undo = "production" in simulated.state()["products"]
                if undo and backup_path(args.plugin).is_dir():
                    rollback_plugin(args.plugin, dry_run=True)

                def restore_cohort_plugin(
                    _transaction: LifecycleTransaction,
                ) -> None:
                    assert _transaction.previous_cohort is not None
                    _restore_production_source(
                        args,
                        _transaction.previous_cohort / "production",
                        undo_installer_transaction=undo,
                    )

                plugin_operation = restore_cohort_plugin

            else:
                if backup_path(args.plugin).is_dir():
                    rollback_plugin(args.plugin, dry_run=True)

                    def plugin_operation(_transaction: LifecycleTransaction) -> None:
                        rollback_plugin(args.plugin)

                elif transaction_path(args.plugin).is_file():
                    undo_install(args.plugin, dry_run=True)

                    def plugin_operation(_transaction: LifecycleTransaction) -> None:
                        undo_install(args.plugin)

                else:

                    def plugin_operation(_transaction: LifecycleTransaction) -> None:
                        if args.plugin.exists():
                            shutil.rmtree(args.plugin)

        _run_transaction(
            args,
            cohort_operation=operation,
            plugin_operation=plugin_operation,
        )
        print(CohortManager(args.cohort_root).state())
    except (CohortError, LifecycleError, OSError, RuntimeError, shutil.Error) as error:
        print(
            f"Lifecycle operation failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

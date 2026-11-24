#!/usr/bin/env python3
"""Atomic installer, legacy-config migration, and rollback for observ-hermes."""

from __future__ import annotations

import argparse
import compileall
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

HERMES_ROOT = Path(__file__).resolve().parents[1]
if str(HERMES_ROOT) not in sys.path:
    sys.path.insert(0, str(HERMES_ROOT))

from observ_hermes.config import (  # noqa: E402
    ConfigError,
    _build_config,
    config_to_public_dict,
    load_json_config,
    validate_public_config,
)

LEGACY_ALLOWED = {
    "AGENT_ENABLE_TELEMETRY",
    "OBSERV_HERMES_ENABLE_TELEMETRY",
    "OTEL_SERVICE_NAME",
    "OBSERV_HERMES_SERVICE_NAME",
    "AIVISION_COLLECTION_MODE",
    "OBSERV_HERMES_COLLECTION_MODE",
    "AIVISION_ANT_USERNAME",
    "ANT_USERNAME",
    "OBSERV_HERMES_USERNAME",
    "AIVISION_ANT_UID",
    "AIVISION_USER_ID",
    "OBSERV_HERMES_USER_ID",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OBSERV_HERMES_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OBSERV_HERMES_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OBSERV_HERMES_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OBSERV_HERMES_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OBSERV_HERMES_OTLP_TRACES_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OBSERV_HERMES_OTLP_METRICS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OBSERV_HERMES_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    "OBSERV_HERMES_OTLP_LOGS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    "OBSERV_HERMES_OTLP_LOGS_HEADERS",
    "OTEL_METRIC_EXPORT_INTERVAL",
    "OBSERV_HERMES_METRIC_EXPORT_INTERVAL",
    "OTEL_LOGS_EXPORT_INTERVAL",
    "OBSERV_HERMES_LOGS_EXPORT_INTERVAL",
    "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
    "OBSERV_HERMES_METRICS_TEMPORALITY_PREFERENCE",
    "OTEL_RESOURCE_ATTRIBUTES",
    "AIVISION_OTEL_RESOURCE_ATTRIBUTES",
    "OBSERV_HERMES_OTEL_RESOURCE_ATTRIBUTES",
    "AIVISION_OTEL_ATTRIBUTES",
    "OBSERV_HERMES_OTEL_ATTRIBUTES",
    "AIVISION_AGENT_TYPE",
    "OBSERV_HERMES_AGENT_TYPE",
    "AIVISION_AGENT_PRODUCT",
    "OBSERV_HERMES_AGENT_PRODUCT",
    "AIVISION_AGENT_INSTANCE",
    "OBSERV_HERMES_AGENT_INSTANCE",
    "AIVISION_AGENT_ENV",
    "OBSERV_HERMES_AGENT_ENV",
    "AIVISION_AGENT_SCENE",
    "OBSERV_HERMES_AGENT_SCENE",
    "AIVISION_DYNAMIC_USERNAME",
    "OBSERV_HERMES_DYNAMIC_USERNAME",
    "AIVISION_DYNAMIC_UID",
    "OBSERV_HERMES_DYNAMIC_UID",
}


@dataclass(frozen=True)
class ConfigPlan:
    destination: Path | None
    content: bytes | None
    legacy_source: Path | None
    legacy_backup: Path | None
    migrated_keys: tuple[str, ...] = ()
    ignored_keys: tuple[str, ...] = ()

    @property
    def creates_config(self) -> bool:
        return self.destination is not None and self.content is not None

    @property
    def migrates_legacy(self) -> bool:
        return self.legacy_source is not None and self.legacy_backup is not None


@dataclass(frozen=True)
class ConfigCommit:
    destination: Path
    digest: str
    legacy_source: Path | None
    legacy_backup: Path | None


def validate_package(package: Path) -> None:
    required = ("__init__.py", "plugin.yaml", "semantic.py")
    missing = [name for name in required if not (package / name).is_file()]
    if missing:
        raise RuntimeError(f"staged plugin missing: {', '.join(missing)}")
    if not compileall.compile_dir(package, quiet=1, force=True):
        raise RuntimeError("staged plugin failed Python compilation")


def backup_path(destination: Path) -> Path:
    """Keep rollback state outside Hermes' recursively scanned plugin root."""
    plugin_root = destination.parent
    return plugin_root.parent / "plugin-backups" / f"{destination.name}.previous"


def transaction_path(destination: Path) -> Path:
    return backup_path(destination).with_name(f"{destination.name}.transaction.json")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_legacy_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RuntimeError(
            f"cannot read legacy config {path}: {type(error).__name__}"
        ) from error
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise RuntimeError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key:
            raise RuntimeError(f"{path}:{line_number}: empty key")
        values[key] = value.strip()
    return values


def _private_json_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def prepare_config_plan(
    *,
    template: Path | None,
    destination: Path | None,
    legacy_env: Path | None,
) -> ConfigPlan:
    if destination is None:
        return ConfigPlan(None, None, None, None)
    destination = destination.expanduser().resolve()
    legacy = legacy_env.expanduser().resolve() if legacy_env else None
    legacy_backup = destination.parent / "config-backups" / "observ-hermes.env.previous"

    if legacy and legacy.exists() and legacy_backup.exists():
        raise RuntimeError(
            f"legacy source and retained backup both exist: {legacy}, {legacy_backup}"
        )
    if destination.exists():
        load_json_config(destination)
        ignored = (
            tuple(sorted(_read_legacy_env(legacy)))
            if legacy and legacy.exists()
            else ()
        )
        return ConfigPlan(destination, None, None, None, ignored_keys=ignored)
    if template is None:
        raise RuntimeError(
            "config template is required when production config is absent"
        )

    template_value = load_json_config(template.expanduser().resolve())
    if not legacy or not legacy.exists():
        return ConfigPlan(
            destination,
            _private_json_bytes(validate_public_config(template_value, str(template))),
            None,
            None,
        )

    legacy_values = _read_legacy_env(legacy)
    allowed = {
        key: value for key, value in legacy_values.items() if key in LEGACY_ALLOWED
    }
    ignored = tuple(sorted(set(legacy_values) - set(allowed)))
    migrated = tuple(sorted(allowed))
    try:
        resolved = _build_config(template_value, {}, environ=allowed)
        public = config_to_public_dict(resolved)
        validate_public_config(public, "migrated config")
    except ConfigError as error:
        raise RuntimeError(f"legacy config migration failed: {error}") from error
    return ConfigPlan(
        destination,
        _private_json_bytes(public),
        legacy,
        legacy_backup,
        migrated_keys=migrated,
        ignored_keys=ignored,
    )


def commit_config(plan: ConfigPlan) -> ConfigCommit | None:
    if not plan.creates_config:
        return None
    assert plan.destination is not None and plan.content is not None
    destination = plan.destination
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    operation_directory = Path(
        tempfile.mkdtemp(prefix=".observ-hermes-config-commit-", dir=destination.parent)
    )
    temporary = None
    destination_created = False
    legacy_moved = False
    try:
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=".observ-hermes.json.", dir=destination.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(temporary_fd, "wb") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(plan.content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        destination_created = True
        if os.name == "posix":
            os.chmod(destination, 0o600)
        if plan.migrates_legacy:
            assert plan.legacy_source is not None and plan.legacy_backup is not None
            plan.legacy_backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(plan.legacy_source, plan.legacy_backup)
            legacy_moved = True
            if os.name == "posix":
                os.chmod(plan.legacy_backup, 0o600)
        commit = ConfigCommit(
            destination=destination,
            digest=_digest(plan.content),
            legacy_source=plan.legacy_source,
            legacy_backup=plan.legacy_backup,
        )
    except Exception as commit_error:
        recovery_errors: list[Exception] = []

        if destination_created and destination.exists():
            try:
                if destination.read_bytes() != plan.content:
                    raise RuntimeError(
                        f"recovery refused to move changed config: {destination}"
                    )
                os.replace(destination, operation_directory / "created-config")
            except Exception as error:
                recovery_errors.append(error)

        if legacy_moved:
            assert plan.legacy_source is not None and plan.legacy_backup is not None
            if plan.legacy_source.exists() or plan.legacy_source.is_symlink():
                recovery_errors.append(
                    RuntimeError(
                        f"recovery destination already exists: {plan.legacy_source}"
                    )
                )
            elif (
                not plan.legacy_backup.exists() and not plan.legacy_backup.is_symlink()
            ):
                recovery_errors.append(
                    RuntimeError(f"legacy backup is missing: {plan.legacy_backup}")
                )
            else:
                try:
                    plan.legacy_source.parent.mkdir(
                        parents=True, exist_ok=True, mode=0o700
                    )
                    os.replace(plan.legacy_backup, plan.legacy_source)
                except Exception as error:
                    recovery_errors.append(error)

        if recovery_errors:
            _raise_recovery_failure(
                "config commit",
                commit_error,
                recovery_errors,
                operation_directory,
            )
        shutil.rmtree(operation_directory, ignore_errors=True)
        raise
    else:
        shutil.rmtree(operation_directory, ignore_errors=True)
        return commit
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def rollback_config_commit(commit: ConfigCommit | None) -> None:
    if commit is None:
        return
    if commit.legacy_backup and commit.legacy_source and commit.legacy_backup.exists():
        commit.legacy_source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(commit.legacy_backup, commit.legacy_source)
    commit.destination.unlink(missing_ok=True)


def _write_transaction(destination: Path, commit: ConfigCommit | None) -> None:
    path = transaction_path(destination)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    value = {
        "version": 1,
        "configCreated": commit is not None,
        "configPath": str(commit.destination) if commit else "",
        "configSha256": commit.digest if commit else "",
        "legacySource": str(commit.legacy_source)
        if commit and commit.legacy_source
        else "",
        "legacyBackup": str(commit.legacy_backup)
        if commit and commit.legacy_backup
        else "",
    }
    content = _private_json_bytes(value)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            if os.name == "posix":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
        os.replace(temporary, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def install(
    source: Path,
    destination: Path,
    *,
    config_template: Path | None = None,
    config_destination: Path | None = None,
    legacy_env: Path | None = None,
    fail_phase: str = "",
) -> Path | None:
    source = source.resolve()
    destination = destination.expanduser().resolve()
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = backup_path(destination)
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    legacy_backup = destination.with_name(f"{destination.name}.previous")
    legacy_archive = backup.with_name(f"{destination.name}.legacy-previous")
    previous: Path | None = None

    config_plan = prepare_config_plan(
        template=config_template,
        destination=config_destination,
        legacy_env=legacy_env,
    )

    config_commit: ConfigCommit | None = None
    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}-stage-", dir=parent
    ) as temporary:
        staged = Path(temporary) / destination.name
        if fail_phase == "copy":
            raise RuntimeError("simulated copy failure")
        shutil.copytree(
            source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        validate_package(staged)
        if fail_phase == "validate":
            raise RuntimeError("simulated validation failure")

        transaction_directory = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}-install-", dir=backup.parent)
        )
        retained_backup = transaction_directory / "previous-backup"
        retained_archive = transaction_directory / "previous-legacy-archive"
        swapped = False
        destination_displaced = False
        backup_displaced = False
        legacy_archived = False
        archive_displaced = False
        try:
            config_commit = commit_config(config_plan)
            if fail_phase == "config":
                raise RuntimeError("simulated config failure")
            if backup.exists():
                os.replace(backup, retained_backup)
                backup_displaced = True
            if destination.exists():
                os.replace(destination, backup)
                previous = backup
                destination_displaced = True
            if legacy_backup.exists():
                if legacy_archive.exists():
                    os.replace(legacy_archive, retained_archive)
                    archive_displaced = True
                os.replace(legacy_backup, legacy_archive)
                legacy_archived = True
            if fail_phase == "swap":
                raise RuntimeError("simulated swap failure")
            os.replace(staged, destination)
            swapped = True
            _write_transaction(destination, config_commit)
        except Exception as install_error:
            recovery_errors: list[Exception] = []

            def recover(action: Callable[[], object]) -> None:
                try:
                    action()
                except Exception as error:
                    recovery_errors.append(error)

            if swapped and destination.exists():
                recover(lambda: _remove_path(destination))
            if destination_displaced and backup.exists():
                recover(lambda: os.replace(backup, destination))
            if backup_displaced and retained_backup.exists():
                recover(lambda: os.replace(retained_backup, backup))
            if legacy_archived and legacy_archive.exists():
                recover(lambda: os.replace(legacy_archive, legacy_backup))
            if archive_displaced and retained_archive.exists():
                recover(lambda: os.replace(retained_archive, legacy_archive))
            recover(lambda: rollback_config_commit(config_commit))
            if recovery_errors:
                details = "; ".join(
                    f"{type(error).__name__}: {error}" for error in recovery_errors
                )
                raise RuntimeError(
                    f"installer recovery failed after {type(install_error).__name__}: "
                    f"{install_error}; {details}; recovery state retained at "
                    f"{transaction_directory}"
                ) from install_error
            shutil.rmtree(transaction_directory, ignore_errors=True)
            raise
        else:
            shutil.rmtree(transaction_directory, ignore_errors=True)

    if config_plan.ignored_keys:
        print("Ignored legacy keys: " + ", ".join(config_plan.ignored_keys))
    if config_plan.migrated_keys:
        print("Migrated legacy keys: " + ", ".join(config_plan.migrated_keys))
    return previous


def _load_transaction(destination: Path) -> dict[str, object]:
    path = transaction_path(destination)
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid installer transaction at {path}")
    return value


def _validate_transaction(
    destination: Path,
) -> tuple[dict[str, object], Path | None, Path | None, Path | None]:
    transaction = _load_transaction(destination)
    if not transaction:
        raise RuntimeError(
            f"no installer transaction at {transaction_path(destination)}"
        )
    config_path_raw = str(transaction.get("configPath", ""))
    config_path = Path(config_path_raw).expanduser() if config_path_raw else None
    config_created = transaction.get("configCreated") is True and bool(config_path_raw)
    if config_created:
        assert config_path is not None
        if not config_path.is_file():
            raise RuntimeError(f"installed config is missing: {config_path}")
        expected = str(transaction.get("configSha256", ""))
        actual = _digest(config_path.read_bytes())
        if actual != expected:
            raise RuntimeError(f"installed config changed since install: {config_path}")
    legacy_source_raw = str(transaction.get("legacySource", ""))
    legacy_backup_raw = str(transaction.get("legacyBackup", ""))
    legacy_source = Path(legacy_source_raw) if legacy_source_raw else None
    retained_legacy = Path(legacy_backup_raw) if legacy_backup_raw else None
    if retained_legacy and not retained_legacy.is_file():
        raise RuntimeError(f"legacy backup is missing: {retained_legacy}")
    if retained_legacy and legacy_source and legacy_source.exists():
        raise RuntimeError(f"legacy source already exists: {legacy_source}")
    return (
        transaction,
        config_path if config_created else None,
        legacy_source,
        retained_legacy,
    )


def _reverse_moves(moves: list[tuple[Path, Path]]) -> list[Exception]:
    errors: list[Exception] = []
    for source, target in reversed(moves):
        if not target.exists() and not target.is_symlink():
            continue
        if source.exists() or source.is_symlink():
            errors.append(
                RuntimeError(f"recovery destination already exists: {source}")
            )
            continue
        try:
            source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(target, source)
        except Exception as error:
            errors.append(error)
    return errors


def _raise_recovery_failure(
    operation: str,
    operation_error: Exception,
    recovery_errors: list[Exception],
    recovery_path: Path,
) -> None:
    details = "; ".join(f"{type(error).__name__}: {error}" for error in recovery_errors)
    raise RuntimeError(
        f"{operation} recovery failed after {type(operation_error).__name__}: "
        f"{operation_error}; {details}; recovery state retained at {recovery_path}"
    ) from operation_error


def _staged_file(path: Path, operation_directory: Path) -> Path:
    return path.with_name(f".{path.name}.{operation_directory.name}")


def undo_install(
    destination: Path,
    *,
    dry_run: bool = False,
    fail_phase: str = "",
) -> None:
    """Undo a fresh install even when there was no previous plugin directory."""
    destination = destination.expanduser().resolve()
    _, config_path, legacy_source, retained_legacy = _validate_transaction(destination)
    if dry_run:
        return

    operation_directory = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}-undo-", dir=backup_path(destination).parent
        )
    )
    transaction = transaction_path(destination)
    staged_config = (
        _staged_file(config_path, operation_directory) if config_path else None
    )
    staged_transaction = _staged_file(transaction, operation_directory)
    moves: list[tuple[Path, Path]] = []

    def move(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(source, target)
        moves.append((source, target))

    try:
        if destination.exists() or destination.is_symlink():
            move(destination, operation_directory / "installed-plugin")
        if config_path and staged_config:
            move(config_path, staged_config)
            if fail_phase == "config":
                raise RuntimeError("simulated undo config removal failure")
        if retained_legacy and legacy_source:
            move(retained_legacy, legacy_source)
            if fail_phase == "legacy":
                raise RuntimeError("simulated undo legacy restoration failure")
        move(transaction, staged_transaction)
    except Exception as operation_error:
        recovery_errors = _reverse_moves(moves)
        if recovery_errors:
            _raise_recovery_failure(
                "undo",
                operation_error,
                recovery_errors,
                operation_directory,
            )
        shutil.rmtree(operation_directory, ignore_errors=True)
        raise

    if staged_config:
        staged_config.unlink(missing_ok=True)
    staged_transaction.unlink(missing_ok=True)
    shutil.rmtree(operation_directory, ignore_errors=True)


def rollback(
    destination: Path,
    *,
    dry_run: bool = False,
    fail_phase: str = "",
) -> None:
    destination = destination.expanduser().resolve()
    backup = backup_path(destination)
    if not backup.is_dir():
        raise RuntimeError(f"no recoverable previous plugin at {backup}")
    _, config_path, legacy_source, retained_legacy = _validate_transaction(destination)
    if dry_run:
        return

    operation_directory = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-rollback-", dir=backup.parent)
    )
    transaction = transaction_path(destination)
    staged_config = (
        _staged_file(config_path, operation_directory) if config_path else None
    )
    staged_transaction = _staged_file(transaction, operation_directory)
    moves: list[tuple[Path, Path]] = []

    def move(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(source, target)
        moves.append((source, target))

    try:
        if destination.exists() or destination.is_symlink():
            move(destination, operation_directory / "installed-plugin")
        move(backup, destination)
        if config_path and staged_config:
            move(config_path, staged_config)
            if fail_phase == "config":
                raise RuntimeError("simulated rollback config removal failure")
        if retained_legacy and legacy_source:
            move(retained_legacy, legacy_source)
            if fail_phase == "legacy":
                raise RuntimeError("simulated rollback legacy restoration failure")
        move(transaction, staged_transaction)
    except Exception as operation_error:
        recovery_errors = _reverse_moves(moves)
        if recovery_errors:
            _raise_recovery_failure(
                "rollback",
                operation_error,
                recovery_errors,
                operation_directory,
            )
        shutil.rmtree(operation_directory, ignore_errors=True)
        raise

    if staged_config:
        staged_config.unlink(missing_ok=True)
    staged_transaction.unlink(missing_ok=True)
    shutil.rmtree(operation_directory, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--source", required=True, type=Path)
    install_parser.add_argument("--destination", required=True, type=Path)
    install_parser.add_argument("--config-template", type=Path)
    install_parser.add_argument("--config-destination", type=Path)
    install_parser.add_argument("--legacy-env", type=Path)
    install_parser.add_argument(
        "--fail-phase",
        choices=("", "copy", "validate", "config", "swap"),
        default=os.environ.get("OBSERV_HERMES_INSTALL_FAIL_PHASE", ""),
        help=argparse.SUPPRESS,
    )
    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--destination", required=True, type=Path)
    rollback_parser.add_argument(
        "--fail-phase",
        choices=("", "config", "legacy"),
        default=os.environ.get("OBSERV_HERMES_ROLLBACK_FAIL_PHASE", ""),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            previous = install(
                args.source,
                args.destination,
                config_template=args.config_template,
                config_destination=args.config_destination,
                legacy_env=args.legacy_env,
                fail_phase=args.fail_phase,
            )
            print(f"Installed plugin -> {args.destination}")
            if previous:
                print(f"Previous plugin retained -> {previous}")
        else:
            rollback(args.destination, fail_phase=args.fail_phase)
            print(f"Restored previous plugin -> {args.destination}")
    except (
        ConfigError,
        OSError,
        RuntimeError,
        shutil.Error,
        json.JSONDecodeError,
    ) as error:
        print(
            f"Install operation failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

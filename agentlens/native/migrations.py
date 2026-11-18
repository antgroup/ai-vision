"""Alembic entry point for source and native-extension migration directories.

The source development ``alembic`` command remains unchanged. Native distributions
compile this entry point and the original environment/revision modules; no generated
migration stubs or bytecode copies are needed. The adapter is tested against the
Alembic version in uv.lock because revision loading uses ScriptDirectory internals.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import Script, ScriptDirectory


def _artifacts(directory: Path) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        name = None
        for suffix in [*importlib.machinery.EXTENSION_SUFFIXES, ".py"]:
            if path.name.endswith(suffix):
                name = path.name[: -len(suffix)]
                break
        if name is None or name.startswith("__"):
            continue
        if name in artifacts:
            raise RuntimeError(f"multiple artifacts for migration module {name}")
        artifacts[name] = path
    return artifacts


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration module {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeScriptDirectory(ScriptDirectory):
    @classmethod
    def from_config(cls, config: Config) -> NativeScriptDirectory:
        # Alembic's factory constructs ScriptDirectory directly, ignoring cls.
        directory = ScriptDirectory.from_config(config)
        if directory.recursive_version_locations:
            raise ValueError("native migrations require flat version directories")
        return cls(
            directory.dir,
            version_locations=directory.version_locations,
            messaging_opts=directory.messaging_opts,
        )

    def _load_revisions(self):
        for directory in self._version_locations:
            if not directory.exists():
                continue
            for name, path in _artifacts(directory).items():
                module = _load(name, path)
                yield Script(module, module.revision, path)

    def run_env(self) -> None:
        path = _artifacts(Path(self.dir))["env"]
        _load("env", path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="obschema/alembic.ini")
    parser.add_argument(
        "command", choices=("upgrade", "downgrade", "heads", "current", "read-org-id-file")
    )
    parser.add_argument("revision", nargs="?")
    args = parser.parse_args()
    if args.command == "read-org-id-file":
        from agentlens.bootstrap import main as bootstrap

        bootstrap()
        return
    config = Config(args.config)
    directory = NativeScriptDirectory.from_config(config)
    if args.command == "heads":
        for revision in directory.get_heads():
            print(revision)
        return
    if args.command in {"upgrade", "downgrade"} and args.revision is None:
        parser.error("upgrade/downgrade requires a revision")

    def migrate(revision, context):
        if args.command == "upgrade":
            return directory._upgrade_revs(args.revision, revision)
        if args.command == "downgrade":
            return directory._downgrade_revs(args.revision, revision)
        for current in context.get_current_heads():
            print(current)
        return []

    with EnvironmentContext(config, directory, fn=migrate, destination_rev=args.revision):
        directory.run_env()


if __name__ == "__main__":
    main()

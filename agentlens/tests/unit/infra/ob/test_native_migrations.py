import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_path = Path(__file__).resolve().parents[4] / "native" / "migrations.py"
_spec = importlib.util.spec_from_file_location("agentlens_migrations", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
NativeScriptDirectory = _module.NativeScriptDirectory


def test_source_revision_graph_matches_standard_alembic():
    migrations = Path(__file__).resolve().parents[4] / "obschema" / "migrations"
    standard = ScriptDirectory(str(migrations))
    native = NativeScriptDirectory(str(migrations))
    expected = [(item.revision, item.down_revision) for item in standard.walk_revisions()]
    assert [(item.revision, item.down_revision) for item in native.walk_revisions()] == expected
    assert native.get_heads() == ["012"]


def test_duplicate_revision_source_and_native_are_rejected(tmp_path):
    import importlib.machinery

    import pytest

    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "001_example.py").write_text("revision = '001'\ndown_revision = None\n")
    (versions / ("001_example" + importlib.machinery.EXTENSION_SUFFIXES[0])).touch()
    with pytest.raises(RuntimeError, match="multiple artifacts"):
        NativeScriptDirectory(str(tmp_path)).get_heads()


def test_config_factory_preserves_native_revision_loader():
    migrations = Path(__file__).resolve().parents[4] / "obschema" / "migrations"
    config = Config()
    config.set_main_option("script_location", str(migrations))
    directory = NativeScriptDirectory.from_config(config)
    assert isinstance(directory, NativeScriptDirectory)
    assert directory.get_heads() == ["012"]

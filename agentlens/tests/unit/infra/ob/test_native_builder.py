import importlib.util
from pathlib import Path

import pytest


def _builder():
    path = Path(__file__).resolve().parents[4] / "native" / "build.py"
    spec = importlib.util.spec_from_file_location("agentlens_native_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dependency_lock_mismatch_preserves_existing_stage(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").write_text("current")
    lock = tmp_path / "dependency.lock"
    lock.write_text("stale")
    output = tmp_path / "output"
    sentinel = output / "runtime" / "sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.touch()
    with pytest.raises(ValueError, match="dependency lock"):
        _builder().build_native(
            source,
            output,
            dependency_venv=tmp_path / "venv",
            dependency_lock=lock,
            replace_runtime=True,
        )
    assert sentinel.exists()


@pytest.mark.parametrize("lib64_alias", [True, False])
def test_owned_package_install_is_removed_from_dependency_copy(tmp_path, monkeypatch, lib64_alias):
    source = tmp_path / "venv"
    site = source / "lib/python3.11/site-packages"
    (site / "agentlens").mkdir(parents=True)
    (site / "agentlens" / "secret.py").write_text("private = True")
    (site / "agentlens-0.1.0.dist-info").mkdir()
    (site / "_editable_impl_agentlens.pth").touch()
    (site / "__editable___agentlens_0_1_0_finder.py").write_text("private = True")
    (site / "__pycache__").mkdir()
    (site / "__pycache__/__editable___agentlens_0_1_0_finder.cpython-311.pyc").touch()
    if lib64_alias:
        (source / "lib64").symlink_to("lib", target_is_directory=True)
    else:
        extra = source / "lib64/python3.11/site-packages/extra_dependency.py"
        extra.parent.mkdir(parents=True)
        extra.write_text("extra = True")
    (site / "alembic").mkdir()
    upstream = site / "alembic" / "__init__.py"
    upstream.write_text("upstream = True")
    target = tmp_path / "runtime-venv"
    builder = _builder()
    monkeypatch.setattr(builder.sys, "platlibdir", "lib")
    builder.copy_dependencies(source, target)
    assert (target / upstream.relative_to(source)).read_bytes() == upstream.read_bytes()
    assert not (target / "lib/python3.11/site-packages/agentlens").exists()
    assert not list(target.rglob("*agentlens*"))
    if lib64_alias:
        assert "lib64" not in {path.name for path in target.iterdir()}
        assert (source / "lib64").is_symlink()
    else:
        assert (target / extra.relative_to(source)).read_bytes() == extra.read_bytes()


def test_symlink_runtime_cannot_replace_unrelated_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").touch()
    lock = tmp_path / "dependency.lock"
    lock.touch()
    output = tmp_path / "output"
    output.mkdir()
    valuable = tmp_path / "valuable"
    valuable.mkdir()
    sentinel = valuable / "sentinel"
    sentinel.touch()
    (output / "runtime").symlink_to(valuable, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _builder().build_native(
            source,
            output,
            dependency_venv=tmp_path / "venv",
            dependency_lock=lock,
            replace_runtime=True,
        )
    assert sentinel.exists()


def test_canonical_bootstrap_input_is_required_before_compilation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").touch()
    lock = tmp_path / "dependency.lock"
    lock.touch()
    venv = tmp_path / "venv"
    venv.mkdir()
    with pytest.raises(ValueError, match="database-init source"):
        _builder().build_native(
            source,
            tmp_path / "output",
            dependency_venv=venv,
            dependency_lock=lock,
            database_init_source=tmp_path / "absent.py",
        )


@pytest.mark.parametrize("overlap", ["source", "runtime", "dependencies", "dependency_parent"])
def test_build_paths_cannot_overlap_inputs_or_runtime(tmp_path, overlap):
    source = tmp_path / "source"
    source.mkdir()
    (source / "uv.lock").touch()
    lock = tmp_path / "dependency.lock"
    lock.touch()
    dependencies = tmp_path / "dependencies"
    dependencies.mkdir()
    output = dependencies / "output" if overlap == "dependency_parent" else tmp_path / "output"
    runtime = output / "runtime"
    runtime.mkdir(parents=True)
    sentinel = runtime / "sentinel"
    sentinel.touch()
    helper = tmp_path / "database-init.py"
    helper.write_text("def main(): pass\n")
    build = {
        "source": source / "build",
        "runtime": runtime / "build",
        "dependencies": dependencies / "build",
        "dependency_parent": tmp_path / "build",
    }[overlap]
    with pytest.raises(ValueError, match="overlap|outside"):
        _builder().build_native(
            source,
            output,
            dependency_venv=dependencies,
            dependency_lock=lock,
            database_init_source=helper,
            build_directory=build,
            replace_runtime=True,
        )
    assert sentinel.exists()

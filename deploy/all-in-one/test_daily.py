import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from daily import stage_export, stage_metadata, validate_payload


def exported(*entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as writer:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            if isinstance(data, str):
                member.type = tarfile.SYMTYPE
                member.linkname = data
                writer.addfile(member)
            else:
                member.size = len(data)
                member.mode = 0o755
                writer.addfile(member, io.BytesIO(data))
    stream.seek(0)
    return stream


class DailyPayloadTests(unittest.TestCase):
    def test_backend_relocates_its_entrypoint_and_never_materializes_archive_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            rootfs = Path(temporary)
            with exported(
                ("app/manage.py", b"manage code"),
                ("app/docker-entrypoint.sh", b"ignored source duplicate"),
                ("app/.venv/bin/python", "/usr/local/bin/python3.11"),
                ("app/.venv/bin/python3", "python"),
                ("app/.venv/lib64", "lib"),
                ("usr/local/bin/docker-entrypoint.sh", b"#!/bin/bash\ncd /app\nexec python manage.py\n"),
                ("etc/passwd", b"unrelated"),
            ) as stream:
                stage_export(stream, rootfs, "aivision-be")
            validate_payload(rootfs, "aivision-be")
            backend = rootfs / "opt/aivision/backend"
            self.assertEqual((backend / "manage.py").read_bytes(), b"manage code")
            self.assertIn(b"\ncd /opt/aivision/backend\n", (backend / "docker-entrypoint.sh").read_bytes())
            self.assertFalse((backend / ".venv").exists())
            self.assertFalse((rootfs / "etc/passwd").exists())

    def test_unexpected_source_links_do_not_cross_the_staging_boundary(self):
        for name, link in (("app/a.py", "../../somewhere"), ("app/.venv/bin/python", "/tmp/python")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                with exported((name, link)) as stream, self.assertRaisesRegex(ValueError, "unexpected daily image member"):
                    stage_export(stream, Path(temporary), "aivision-be")

    def test_incomplete_lifecycle_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError, "AgentLens"):
            validate_payload(Path(temporary), "agentlens")

    def test_observed_backend_build_must_match_qualified_manifest_before_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rootfs = root / "rootfs"
            metadata = rootfs / "opt/aivision/backend/aivision_backend/build-info.json"
            metadata.parent.mkdir(parents=True)
            observed = {"build_id": "42.1"}
            metadata.write_text(json.dumps(observed))
            manifest = {"component_builds": {"aivision-be": observed}}
            accepted = root / "release-manifest.json"
            accepted.write_text(json.dumps(manifest))

            stage_metadata(rootfs, root, manifest)
            self.assertEqual((rootfs / "etc/aivision/build-info.json").read_bytes(), metadata.read_bytes())
            self.assertEqual((rootfs / "etc/aivision/release-manifest.json").read_bytes(), accepted.read_bytes())

            metadata.write_text(json.dumps({"build_id": "41.1"}))
            with self.assertRaisesRegex(ValueError, "differs from the frozen Daily"):
                stage_metadata(rootfs, root, manifest)

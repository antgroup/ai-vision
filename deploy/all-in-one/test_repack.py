"""Check that preview re-compression preserves the audited Docker archive."""

import gzip
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("repack.py")
NEW_TAG = "all-in-one-preview-v0.1.0-daily.20261117"


def sha(data):
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(shutil.which("zstd"), "the packaging host needs zstd")
class RepackTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source = root / "original"
        self.source.mkdir()
        self.output = root / "candidate"
        self.installer = root / "installer.py"
        self.installer.write_text("# installer\n", encoding="utf-8")
        self.sample = root / "sample.py"
        self.sample.write_text("# sample trace\n", encoding="utf-8")
        for arch, suffix, cli_arch in (("amd64", "", "x64"), ("arm64", "-arm64", "arm64")):
            image = f"aiv-all-in-one-linux-{arch}.tar.gz"
            cli = f"aiv-linux-{cli_arch}.tar.gz"
            archive = io.BytesIO()
            config = json.dumps({"os": "linux", "architecture": arch}).encode()
            with tarfile.open(fileobj=archive, mode="w") as image_tar:
                for name, payload in (("config.json", config), ("manifest.json", b"[]")):
                    member = tarfile.TarInfo(name)
                    member.size = len(payload)
                    image_tar.addfile(member, io.BytesIO(payload))
            image_bytes = gzip.compress(archive.getvalue(), mtime=0)
            cli_bytes = gzip.compress(arch.encode(), mtime=0)
            (self.source / image).write_bytes(image_bytes)
            (self.source / cli).write_bytes(cli_bytes)
            manifest = {
                "schema_version": 1, "profile": "all-in-one-preview", "platform": f"linux/{arch}",
                "release_tag": "all-in-one-preview-v0.1.0-daily.20261113.2",
                "base_release": "delivery-v0.1.0-daily.20261108", "image_id": "sha256:" + sha(config),
                "image_tag": f"aiv-all-in-one:test-{arch}",
                "archive": image, "archive_sha256": sha(image_bytes), "archive_bytes": len(image_bytes),
                "agent_cli": {
                    "archive": cli, "archive_sha256": sha(cli_bytes), "archive_bytes": len(cli_bytes),
                    "binary_sha256": sha(arch.encode()), "version": "0.1.0-daily.20261108",
                },
            }
            (self.source / f"all-in-one-manifest{suffix}.json").write_text(json.dumps(manifest), encoding="utf-8")
        (self.source / "SHA256SUMS").write_text("".join(
            f"{sha(path.read_bytes())}  {path.name}\n" for path in sorted(self.source.iterdir())
        ), encoding="ascii")

    def repack(self):
        return subprocess.run([
            sys.executable, str(SCRIPT), "--source", str(self.source), "--output", str(self.output),
            "--release-tag", NEW_TAG, "--installer", str(self.installer), "--sample-trace", str(self.sample),
        ], text=True, capture_output=True)

    def test_both_image_archives_keep_the_exact_tar_and_original_gzip(self):
        completed = self.repack()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for suffix in ("", "-arm64"):
            manifest_name = f"all-in-one-manifest{suffix}.json"
            original = json.loads((self.source / manifest_name).read_text())
            updated = json.loads((self.output / manifest_name).read_text())
            self.assertEqual(updated["release_tag"], NEW_TAG)
            self.assertEqual(updated["image_id"], original["image_id"])
            self.assertEqual(updated["agent_cli"], original["agent_cli"])
            legacy = (self.source / original["archive"]).read_bytes()
            self.assertEqual((self.output / updated["archive"]).read_bytes(), legacy)
            compact = self.output / updated["zstd_archive"]
            self.assertEqual(sha(compact.read_bytes()), updated["zstd_archive_sha256"])
            self.assertEqual(compact.stat().st_size, updated["zstd_archive_bytes"])
            self.assertEqual(subprocess.check_output(["zstd", "-dc", compact]), gzip.decompress(legacy))
        self.assertEqual((self.output / "install-all-in-one.py").read_bytes(), self.installer.read_bytes())
        self.assertEqual((self.output / "sample-trace.py").read_bytes(), self.sample.read_bytes())
        names = [path for path in sorted(self.output.iterdir()) if path.name != "SHA256SUMS"]
        self.assertEqual((self.output / "SHA256SUMS").read_text().splitlines(), [
            f"{sha(path.read_bytes())}  {path.name}" for path in names
        ])

    def test_modified_arm_archive_cannot_leave_a_partial_package(self):
        with (self.source / "aiv-all-in-one-linux-arm64.tar.gz").open("ab") as stream:
            stream.write(b"unexpected bytes")
        completed = self.repack()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("aiv-all-in-one-linux-arm64.tar.gz", completed.stderr)
        self.assertIn("size", completed.stderr)
        self.assertFalse(self.output.exists())

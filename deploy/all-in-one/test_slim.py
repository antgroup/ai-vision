import csv
import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import slim


OLD = b"\x7fELFloadable bytes and debug data"
NEW = b"\x7fELFloadable bytes"


class SlimTest(unittest.TestCase):
    def test_third_party_wheel_records_follow_the_actual_transformation(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            library = site / "uvloop/loop.so"
            library.parent.mkdir()
            library.write_bytes(OLD)
            wheel = site / "uvloop-0.22.1.dist-info/RECORD"
            wheel.parent.mkdir()
            wheel.write_text(f"uvloop/loop.so,{slim.record_digest(OLD)},{len(OLD)}\n")
            own = site / "agentlens-engine.so"
            own.write_bytes(OLD)
            with patch.object(slim, "strip_debug", return_value=NEW) as strip:
                result = slim.strip_wheels(site, "arm64", "test-strip")
            strip.assert_called_once_with(OLD, "arm64", "test-strip")
            self.assertEqual(own.read_bytes(), OLD)
            self.assertEqual(library.read_bytes(), NEW)
            self.assertEqual(result[0]["after_sha256"], hashlib.sha256(NEW).hexdigest())
            self.assertEqual(list(csv.reader(io.StringIO(wheel.read_text()))), [
                ["uvloop/loop.so", slim.record_digest(NEW), str(len(NEW))],
            ])

    def test_stale_wheel_record_prevents_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            library = site / "uvloop.so"
            library.write_bytes(OLD)
            wheel = site / "uvloop-0.22.1.dist-info/RECORD"
            wheel.parent.mkdir()
            wheel.write_text(f"uvloop.so,{slim.record_digest(NEW)},{len(NEW)}\n")
            with patch.object(slim, "strip_debug", return_value=NEW), self.assertRaisesRegex(ValueError, "RECORD"):
                slim.strip_wheels(site, "arm64", "test-strip")
            self.assertEqual(library.read_bytes(), OLD)

    def test_provider_keeps_business_code_and_tar_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "connect.tar.gz"
            files = ("connect/libpython3.11.so.1.0", "connect/agentlens-engine.so", "connect/connect")
            with tarfile.open(bundle, "w:gz") as writer:
                for name in files:
                    member = tarfile.TarInfo(name)
                    member.size = len(OLD)
                    member.mode = 0o755
                    writer.addfile(member, io.BytesIO(OLD))
            with patch.object(slim, "strip_debug", return_value=NEW) as strip:
                report = slim.strip_x64_provider(bundle, "x86-strip")
            strip.assert_called_once_with(OLD, "amd64", "x86-strip")
            self.assertEqual(len(report["libraries"]), 1)
            with tarfile.open(bundle, "r:gz") as reader:
                self.assertEqual(reader.getnames(), list(files))
                self.assertEqual([reader.extractfile(name).read() for name in files], [NEW, OLD, OLD])
                self.assertTrue(all(item.mode == 0o755 for item in reader))

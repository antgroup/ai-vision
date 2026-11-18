"""Reject source carriers outside the executable in a native CLI archive."""

import gzip
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from native_cli_archive import CLI_MEMBERS, require_archive

ROOT = Path(__file__).resolve().parents[2]


class NativeArchiveBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "aiv-linux-x64.tar.gz"
        self.source = (ROOT / "deploy/bootstrap/backend-bootstrap.py").read_bytes()
        self.probe = b"import sys; sys.exit(sys.version_info < (3, 11))"
        reader = patch("native_cli_archive.read_cli_build_info", return_value={})
        self.read_info = reader.start()
        self.addCleanup(reader.stop)
        validator = patch("native_cli_archive.require_cli_build_info")
        validator.start()
        self.addCleanup(validator.stop)

    def archive(self, mutate=None, *, global_headers=None):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", pax_headers=global_headers or {}) as archive:
            for name in sorted(CLI_MEMBERS):
                data = b"native executable fixture" if name == "aiv" else (ROOT / name).read_bytes()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o755 if name == "aiv" else 0o644
                if mutate:
                    data = mutate(member, data)
                    member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        self.path.write_bytes(gzip.compress(output.getvalue(), mtime=0))

    def require(self):
        return require_archive(self.path, product_version="0.0.0-test", platform="linux-x64",
                               source_sha="a" * 40)

    def rewrite_tar(self, mutate):
        self.path.write_bytes(gzip.compress(mutate(gzip.decompress(self.path.read_bytes())), mtime=0))

    @staticmethod
    def header_with_field(data, offset, width, value):
        header = bytearray(data[:512])
        header[offset:offset + width] = value.ljust(width, b"\0")
        header[148:156] = b"        "
        header[148:156] = ("%06o\0 " % sum(header)).encode()
        return bytes(header) + data[512:]

    def test_original_attribution_bytes_and_structural_archive_are_accepted(self):
        self.archive()
        self.require()
        self.assertEqual(self.read_info.call_count, 1)

    def test_every_attribution_member_requires_exact_trusted_source_bytes(self):
        for name in sorted(CLI_MEMBERS - {"aiv"}):
            for replacement in (b"replaced attribution\n", (ROOT / name).read_bytes() + b"\n" + self.source):
                self.archive(lambda member, data: replacement if member.name == name else data)
                with self.subTest(member=name, appended_source=self.source in replacement):
                    with self.assertRaisesRegex(ValueError, "trusted.*attribution"):
                        self.require()
        self.read_info.assert_not_called()

    def test_pax_member_and_global_metadata_cannot_carry_owned_source(self):
        for global_metadata in (False, True):
            def add_source(member, data):
                if not global_metadata and member.name == "aiv":
                    member.pax_headers = {"delivery.owned-source": self.source.decode()}
                return data
            self.archive(add_source, global_headers={"delivery.owned-source": self.source.decode()} if global_metadata else {})
            with self.subTest(global_metadata=global_metadata), self.assertRaisesRegex(ValueError, "tar metadata"):
                self.require()
        self.read_info.assert_not_called()

    def test_pax_overridden_raw_member_name_is_not_an_uninspected_carrier(self):
        def override_name(member, data):
            if member.name == "aiv":
                member.name = "import private_logic"
                member.pax_headers = {"path": "aiv"}
            return data
        self.archive(override_name)
        with self.assertRaisesRegex(ValueError, "tar metadata"):
            self.require()

    def test_tar_linkname_owner_unused_fields_and_padding_are_inspected(self):
        for label, offset, width, payload in (
            ("linkname", 157, 100, self.probe),
            ("owner", 265, 32, b"import private_logic"),
            ("hidden name suffix", 8, 92, self.probe),
            ("GNU unused prefix", 345, 155, self.probe),
            ("unused padding", 500, 12, b"import x"),
        ):
            self.archive()
            self.rewrite_tar(lambda data: self.header_with_field(data, offset, width, payload))
            if label == "GNU unused prefix":
                self.rewrite_tar(lambda data: self.header_with_field(data, 257, 8, tarfile.GNU_MAGIC))
            with self.subTest(field=label), self.assertRaisesRegex(ValueError, "tar metadata"):
                self.require()
        for label in ("member padding", "trailer"):
            self.archive()
            def hide_in_padding(data):
                if label == "member padding":
                    offset = 512 + len((ROOT / "LICENSE").read_bytes())
                else:
                    offset = len(data) - 512
                return data[:offset] + self.probe + data[offset + len(self.probe):]
            self.rewrite_tar(hide_in_padding)
            with self.subTest(field=label), self.assertRaisesRegex(ValueError, "tar metadata"):
                self.require()

    def test_gnu_longname_record_cannot_hide_an_overridden_member_name(self):
        self.archive()
        raw = gzip.decompress(self.path.read_bytes())
        member = tarfile.TarInfo("././@LongLink")
        member.type = tarfile.GNUTYPE_LONGNAME
        member.size = 8
        header = member.tobuf(format=tarfile.GNU_FORMAT)
        raw = self.header_with_field(raw, 0, 100, self.probe)
        self.path.write_bytes(gzip.compress(header + b"LICENSE\0".ljust(512, b"\0") + raw, mtime=0))
        with self.assertRaisesRegex(ValueError, "tar metadata"):
            self.require()

    def test_gzip_optional_metadata_and_extra_streams_are_rejected(self):
        self.archive()
        original = self.path.read_bytes()
        for flag, metadata in ((8, self.source + b"\0"), (16, self.source + b"\0"),
                               (4, len(self.source).to_bytes(2, "little") + self.source)):
            encoded = original[:3] + bytes([flag]) + original[4:10] + metadata + original[10:]
            self.path.write_bytes(encoded)
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, "gzip metadata"):
                self.require()
        for suffix in (gzip.compress(self.source, mtime=0), self.source):
            self.path.write_bytes(original + suffix)
            with self.subTest(extra_gzip=suffix[:2] == b"\x1f\x8b"), self.assertRaisesRegex(ValueError, "gzip metadata"):
                self.require()


if __name__ == "__main__":
    unittest.main()

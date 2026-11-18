"""Regression fixtures for the AIO ELF transform boundary."""

from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from elf_strip import strip_debug, verify_stripped


TEXT_OFFSET = 0x100
NOTE_OFFSET = 0x118
DYNAMIC_OFFSET = 0x140


def elf(*, debug=False, machine=62, loaded_debug=False):
    """A small ELF64 DSO layout with a single load segment and PT_DYNAMIC."""
    payload = bytearray(0x200)
    payload[TEXT_OFFSET:TEXT_OFFSET + 4] = b"code"
    payload[NOTE_OFFSET:NOTE_OFFSET + 8] = b"elf note"
    names = bytearray(b"\0")
    sections = [bytes(64)]

    def append(name, kind, flags, address, offset, size, alignment=1, entsize=0):
        name_offset = len(names)
        names.extend(name.encode("ascii") + b"\0")
        sections.append(struct.pack("<IIQQQQIIQQ", name_offset, kind, flags,
                                    address, offset, size, 0, 0, alignment, entsize))

    append(".text", 1, 6, TEXT_OFFSET, TEXT_OFFSET, 4)
    append(".note.runtime", 7, 2, NOTE_OFFSET, NOTE_OFFSET, 8)
    append(".dynamic", 6, 3, DYNAMIC_OFFSET, DYNAMIC_OFFSET, 16, 8, 16)
    if loaded_debug:
        append(".debug_loaded", 1, 2, 0x180, 0x180, 4)
        payload[0x180:0x184] = b"keep"
    if debug:
        value = b"discard debugging information" * 32
        append(".debug_info", 1, 0, 0, len(payload), len(value))
        payload.extend(value)
    names_offset = len(payload)
    append(".shstrtab", 3, 0, 0, names_offset, len(names) + len(".shstrtab") + 1)
    payload.extend(names)
    payload.extend(b"\0" * (-len(payload) % 8))
    sections_offset = len(payload)
    payload.extend(b"".join(sections))
    ident = b"\x7fELF\x02\x01\x01\x00" + bytes(8)
    struct.pack_into("<16sHHIQQQIHHHHHH", payload, 0, ident, 3, machine, 1,
                     0, 64, sections_offset, 0, 64, 56, 2, 64,
                     len(sections), len(sections) - 1)
    struct.pack_into("<IIQQQQQQ", payload, 64, 1, 7, 0, 0, 0, 0x200, 0x200, 0x1000)
    struct.pack_into("<IIQQQQQQ", payload, 120, 2, 6, DYNAMIC_OFFSET, DYNAMIC_OFFSET,
                     DYNAMIC_OFFSET, 16, 16, 8)
    return bytes(payload)


class ELFStripTests(unittest.TestCase):
    def test_accepts_only_removing_debug_sections_and_relocating_section_table(self):
        for machine, arch in ((62, "amd64"), (183, "arm64")):
            with self.subTest(arch=arch):
                verify_stripped(elf(debug=True, machine=machine), elf(machine=machine), arch)

    def test_rejects_changed_loaded_contents_even_outside_sections(self):
        for offset in (TEXT_OFFSET, NOTE_OFFSET, DYNAMIC_OFFSET, 0x1A0):
            with self.subTest(offset=offset):
                candidate = bytearray(elf())
                candidate[offset] ^= 1
                with self.assertRaises(ValueError):
                    verify_stripped(elf(debug=True), bytes(candidate), "amd64")

    def test_rejects_changed_memory_mapping(self):
        candidate = bytearray(elf())
        struct.pack_into("<Q", candidate, 64 + 40, 0x300)
        with self.assertRaises(ValueError):
            verify_stripped(elf(debug=True), bytes(candidate), "amd64")

    def test_rejects_deletion_of_allocated_debug_named_section(self):
        with self.assertRaises(ValueError):
            verify_stripped(elf(debug=True, loaded_debug=True), elf(), "amd64")

    def test_already_stripped_file_never_runs_tool(self):
        data = elf(loaded_debug=True)
        self.assertIs(strip_debug(data, "amd64", Path("/nonexistent/strip")), data)

    def test_restores_only_patchelf_fill_outside_sections(self):
        original = bytearray(elf(debug=True))
        original[0x1A0:0x1B0] = b"X" * 16
        with patch("elf_strip.subprocess.run", side_effect=lambda cmd, **_: Path(cmd[4]).write_bytes(elf())):
            candidate = strip_debug(bytes(original), "amd64", "strip")
        self.assertEqual(candidate[0x1A0:0x1B0], b"X" * 16)
        verify_stripped(bytes(original), candidate, "amd64")

    def test_rejects_normalizing_arbitrary_non_section_gap_bytes(self):
        original = bytearray(elf(debug=True))
        original[0x1A0:0x1B0] = b"Y" * 16
        with patch("elf_strip.subprocess.run", side_effect=lambda cmd, **_: Path(cmd[4]).write_bytes(elf())):
            with self.assertRaises(ValueError):
                strip_debug(bytes(original), "amd64", "strip")

    def test_wrong_target_and_truncated_file_fail_before_invoking_tool(self):
        with self.assertRaisesRegex(ValueError, "machine"):
            strip_debug(elf(debug=True, machine=183), "amd64", Path("/nonexistent/strip"))
        with self.assertRaises(ValueError):
            strip_debug(elf(debug=True)[:-5], "amd64", Path("/nonexistent/strip"))


if __name__ == "__main__":
    unittest.main()

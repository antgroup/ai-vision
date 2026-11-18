"""Strip debug sections from reviewed Linux ELF dependencies and check the result."""

from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile
from typing import NamedTuple


ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
MACHINES = {"amd64": 62, "arm64": 183}
PT_LOAD = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_RELA = 4
SHT_NOBITS = 8
SHT_REL = 9
SHT_SYMTAB_SHNDX = 18
SHF_ALLOC = 2
SHF_INFO_LINK = 0x40
DEBUG_PREFIXES = (b".debug_", b".zdebug_", b".stab")


class Segment(NamedTuple):
    kind: int
    flags: int
    offset: int
    address: int
    physical: int
    file_size: int
    memory_size: int
    alignment: int

    def mapping(self):
        return self[:2] + self[3:]


class Section(NamedTuple):
    name: bytes
    kind: int
    flags: int
    address: int
    offset: int
    size: int
    link: int
    info: int
    alignment: int
    entry_size: int

    def identity(self):
        return self.name, self.kind, self.flags, self.address, self.size, self.alignment, self.entry_size


def view(data, offset, size):
    if offset + size > len(data):
        raise ValueError("ELF structure lies outside the file")
    return memoryview(data)[offset:offset + size]


class ELF:
    def __init__(self, data: bytes, arch: str):
        self.data = data
        machine = MACHINES.get(arch.removeprefix("linux/"))
        if machine is None:
            raise ValueError(f"unsupported ELF architecture: {arch}")
        (ident, kind, found_machine, version, entry, program_offset, section_offset, flags,
         header_size, program_size, program_count, section_size, section_count, names_index
         ) = ELF_HEADER.unpack(view(data, 0, ELF_HEADER.size))
        if ident[:7] != b"\x7fELF\x02\x01\x01" or ident[7] not in (0, 3) or version != 1 or kind not in (2, 3):
            raise ValueError("expected a little-endian Linux ELF64 executable or shared object")
        if found_machine != machine:
            raise ValueError(f"ELF machine does not match {arch}")
        if (header_size != ELF_HEADER.size or program_size != PROGRAM_HEADER.size or program_offset < header_size
                or not 0 < program_count < 0xFFFF):
            raise ValueError("unsupported ELF program header layout")
        self.header = ident, kind, found_machine, version, entry, program_offset, flags
        self.segments = tuple(Segment(*raw) for raw in PROGRAM_HEADER.iter_unpack(
            view(data, program_offset, program_count * PROGRAM_HEADER.size)))
        loads = [segment for segment in self.segments if segment.kind == PT_LOAD]
        if not any(segment.file_size for segment in loads):
            raise ValueError("ELF has no loadable file data")
        for segment in self.segments:
            if segment.file_size:
                view(data, segment.offset, segment.file_size)
        for segment in loads:
            if (segment.file_size > segment.memory_size or (segment.alignment > 1 and (
                    segment.alignment & (segment.alignment - 1)
                    or segment.offset % segment.alignment != segment.address % segment.alignment))):
                raise ValueError("invalid ELF load segment mapping")

        # These fields describe file layout. Their uses are checked separately by
        # reading the section table and comparing the bytes mapped by each segment.
        self.file_layout_fields = [(40, 48), (58, 64)]
        self.file_layout_fields.extend((program_offset + index * PROGRAM_HEADER.size + 8,
                                        program_offset + index * PROGRAM_HEADER.size + 16)
                                       for index in range(program_count))
        self.program_end = program_offset + program_count * PROGRAM_HEADER.size
        self.section_offset = section_offset
        self.names_index = names_index
        self.sections = self.read_sections(section_offset, section_size, section_count, names_index)

    def read_sections(self, offset, size, count, names_index):
        if not offset and not count and not names_index and size in (0, SECTION_HEADER.size):
            return ()
        if offset < ELF_HEADER.size or size != SECTION_HEADER.size or not 0 < names_index < count < 0xFF00:
            raise ValueError("unsupported ELF section header layout")
        records = tuple(SECTION_HEADER.iter_unpack(view(self.data, offset, count * size)))
        if any(records[0]) or records[names_index][1] != SHT_STRTAB or records[names_index][2] & SHF_ALLOC:
            raise ValueError("invalid ELF section string table")
        names = bytes(view(self.data, *records[names_index][4:6]))
        if not names or names[0] != 0 or names[-1] != 0:
            raise ValueError("invalid ELF section names")
        sections = []
        for record in records:
            start, *fields = record
            end = names.find(b"\0", start)
            if end == -1:
                raise ValueError("invalid ELF section name offset")
            section = Section(names[start:end], *fields)
            if section.kind != SHT_NOBITS:
                view(self.data, section.offset, section.size)
            sections.append(section)
        return tuple(sections)

    def debug_sections(self):
        debug = {index for index, section in enumerate(self.sections)
                 if not section.flags & SHF_ALLOC and section.name.startswith(DEBUG_PREFIXES)}
        # Relocations for a removed debug section are debug data too.
        debug.update(index for index, section in enumerate(self.sections)
                     if not section.flags & SHF_ALLOC and section.kind in (SHT_REL, SHT_RELA)
                     and section.info in debug)
        for index in debug:
            section = self.sections[index]
            if section.kind == SHT_NOBITS or any(segment.file_size and section.size
                    and section.offset < segment.offset + segment.file_size
                    and segment.offset < section.offset + section.size for segment in self.segments):
                raise ValueError("ELF debug section overlaps program data or has no file payload")
        return debug

    def segment_signature(self, segment):
        digest = hashlib.sha256()
        file_end = segment.offset + segment.file_size
        cursor = segment.offset
        for start, end in self.file_layout_fields:
            if cursor < end and start < file_end:
                digest.update(view(self.data, cursor, max(0, start - cursor)))
                skip = min(file_end, end)
                digest.update(bytes(skip - max(cursor, start)))
                cursor = skip
        if cursor < file_end:
            digest.update(view(self.data, cursor, file_end - cursor))
        return digest.digest()

    def protected_spans(self):
        spans = [(0, ELF_HEADER.size), (self.header[5], self.program_end),
                 (self.section_offset, self.section_offset + len(self.sections) * SECTION_HEADER.size)]
        spans.extend((section.offset, section.offset + section.size) for section in self.sections
                     if section.kind != SHT_NOBITS and section.size)
        return spans

    def section_signature(self, debug):
        # GNU strip may rebuild the section names and static symbol tables.
        # The loader-facing dynamic symbols, relocations, notes and other data
        # must keep their bytes, virtual addresses and semantic section links.
        static_symbols = {index for index, section in enumerate(self.sections)
                          if not section.flags & SHF_ALLOC and section.kind == SHT_SYMTAB}
        static_tables = static_symbols | {self.names_index, 0}
        for index in static_symbols:
            table = self.sections[index].link
            if not 0 < table < len(self.sections) or self.sections[table].kind != SHT_STRTAB:
                raise ValueError("invalid ELF static symbol string table")
            if not self.sections[table].flags & SHF_ALLOC:
                static_tables.add(table)
        static_tables.update(index for index, section in enumerate(self.sections)
                             if not section.flags & SHF_ALLOC and section.kind == SHT_SYMTAB_SHNDX
                             and section.link in static_symbols)
        return sorted(self.section_record(section) for index, section in enumerate(self.sections)
                      if index not in debug and index not in static_tables)

    def section_record(self, section):
        def linked(index):
            if index >= len(self.sections):
                raise ValueError("invalid ELF section link")
            return self.sections[index].identity()

        content = (b"" if section.kind == SHT_NOBITS else
                   hashlib.sha256(view(self.data, section.offset, section.size)).digest())
        info_is_link = bool(section.flags & SHF_INFO_LINK or section.kind in (SHT_REL, SHT_RELA))
        info = linked(section.info) if info_is_link else (section.info,)
        return section.identity(), linked(section.link), info, content


def verify_stripped(original: bytes, candidate: bytes, arch: str) -> None:
    """Reject output that changes anything outside debug data and ELF bookkeeping."""
    before, after = ELF(original, arch), ELF(candidate, arch)
    verify_equivalence(before, after, before.debug_sections())


def verify_equivalence(before, after, debug):
    verify_layout(before, after, debug)
    if segment_contents(before) != segment_contents(after):
        raise ValueError("strip changed ELF mapped bytes")


def segment_contents(elf):
    return [elf.segment_signature(segment) for segment in elf.segments]


def verify_layout(before, after, debug):
    if after.debug_sections():
        raise ValueError("strip left ELF debug sections in the output")
    if (before.header != after.header
            or [segment.mapping() for segment in before.segments] != [segment.mapping() for segment in after.segments]):
        raise ValueError("strip changed ELF program mappings")
    if before.section_signature(debug) != after.section_signature(set()):
        raise ValueError("strip changed ELF sections other than debug or static symbol metadata")


def restore_patchelf_padding(before, after, before_contents, after_contents):
    """Restore mapped bytes that patchelf filled with 'X' and GNU strip zeroed."""
    original_protected, candidate_protected = before.protected_spans(), after.protected_spans()
    restored = bytearray(after.data)
    for old, new, old_hash, new_hash in zip(before.segments, after.segments, before_contents, after_contents):
        if old_hash == new_hash:
            continue
        old_bytes = view(before.data, old.offset, old.file_size)
        new_bytes = view(after.data, new.offset, new.file_size)
        for offset, (a, b) in enumerate(zip(old_bytes, new_bytes)):
            if a == b:
                continue
            previous, current = old.offset + offset, new.offset + offset
            if (any(lo <= previous < hi for lo, hi in before.file_layout_fields)
                    and any(lo <= current < hi for lo, hi in after.file_layout_fields)):
                continue
            if (a != ord("X") or b != 0 or any(lo <= previous < hi for lo, hi in original_protected)
                    or any(lo <= current < hi for lo, hi in candidate_protected)):
                raise ValueError("strip changed ELF mapped bytes outside patchelf padding")
            restored[current] = a
    # Full verification of every segment after all writes also checks overlapping
    # mappings such as PT_LOAD and PT_GNU_RELRO for a consistent restoration.
    return bytes(restored)


def strip_debug(data: bytes, arch: str, tool: str | Path) -> bytes:
    """Strip verified non-loaded debug sections, leaving already stripped bytes intact."""
    before = ELF(data, arch)
    debug = before.debug_sections()
    if not debug:
        return data
    with tempfile.TemporaryDirectory(prefix="aiv-aio-strip-") as work:
        original, output = Path(work) / "original.elf", Path(work) / "stripped.elf"
        original.write_bytes(data)
        try:
            subprocess.run([str(tool), "--strip-debug", "--no-merge-notes", "-o", str(output), "--", str(original)],
                           check=True, capture_output=True)
            candidate = output.read_bytes()
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or b"").decode(errors="replace").strip()
            raise ValueError(f"ELF strip failed: {detail}") from error
        except OSError as error:
            raise ValueError(f"cannot run ELF strip: {error}") from error
    after = ELF(candidate, arch)
    verify_layout(before, after, debug)
    original_contents, candidate_contents = segment_contents(before), segment_contents(after)
    if original_contents != candidate_contents:
        candidate = restore_patchelf_padding(before, after, original_contents, candidate_contents)
        verify_equivalence(before, ELF(candidate, arch), debug)
    return candidate

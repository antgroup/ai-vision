#!/usr/bin/env python3
"""Verify the macOS 11.0 deployment target in packaged ARM64 executables."""

import argparse
import os
from pathlib import Path
import struct


MACH_HEADER_64 = struct.Struct('<8I')
LOAD_COMMAND = struct.Struct('<2I')
BUILD_VERSION = struct.Struct('<4I')
VERSION_MIN = struct.Struct('<2I')
MH_MAGIC_64 = 0xFEEDFACF
MH_EXECUTE = 2
CPU_TYPE_ARM64 = 0x0100000C
CPU_SUBTYPE_BITS = 0x00FFFFFF
LC_BUILD_VERSION = 0x32
LC_VERSION_MIN_MACOSX = 0x24
NON_MACOS_VERSION_COMMANDS = {0x25, 0x2F, 0x30}
PLATFORM_MACOS = 1
MAX_MINOS = 11 << 16


def deployment_target(path):
    with path.open('rb') as binary:
        header = binary.read(MACH_HEADER_64.size)
        if len(header) != MACH_HEADER_64.size:
            raise ValueError('incomplete Mach-O header')
        magic, cpu, subtype, filetype, count, size, _, _ = MACH_HEADER_64.unpack(header)
        if (magic != MH_MAGIC_64 or cpu != CPU_TYPE_ARM64
                or (subtype & CPU_SUBTYPE_BITS) not in (0, 1) or filetype != MH_EXECUTE):
            raise ValueError('expected a thin arm64 Mach-O executable')
        commands_end = MACH_HEADER_64.size + size
        if count > size // LOAD_COMMAND.size or commands_end > os.fstat(binary.fileno()).st_size:
            raise ValueError('invalid Mach-O load-command bounds')

        minos = None
        for _ in range(count):
            command_start = binary.tell()
            if command_start + LOAD_COMMAND.size > commands_end:
                raise ValueError('truncated Mach-O load-command header')
            command, length = LOAD_COMMAND.unpack(binary.read(LOAD_COMMAND.size))
            command_end = command_start + length
            if length < LOAD_COMMAND.size or length % 8 or command_end > commands_end:
                raise ValueError('invalid Mach-O load-command size')

            version = None
            if command == LC_BUILD_VERSION:
                if length < LOAD_COMMAND.size + BUILD_VERSION.size:
                    raise ValueError('truncated LC_BUILD_VERSION')
                platform, version, _, tools = BUILD_VERSION.unpack(binary.read(BUILD_VERSION.size))
                if platform != PLATFORM_MACOS:
                    raise ValueError('expected the macOS build platform')
                if length != LOAD_COMMAND.size + BUILD_VERSION.size + 8 * tools:
                    raise ValueError('invalid LC_BUILD_VERSION tool list')
            elif command == LC_VERSION_MIN_MACOSX:
                if length != LOAD_COMMAND.size + VERSION_MIN.size:
                    raise ValueError('invalid LC_VERSION_MIN_MACOSX')
                version, _ = VERSION_MIN.unpack(binary.read(VERSION_MIN.size))
            elif command in NON_MACOS_VERSION_COMMANDS:
                raise ValueError('expected the macOS build platform')

            if version is not None:
                if minos is not None:
                    raise ValueError('multiple macOS deployment targets')
                minos = version
            binary.seek(command_end)

        if binary.tell() != commands_end:
            raise ValueError('Mach-O load commands do not match the header size')
        if not minos:
            raise ValueError('missing macOS deployment target')
        return minos


def version_string(version):
    major = version >> 16
    minor = (version >> 8) & 0xFF
    patch = version & 0xFF
    return f'{major}.{minor}.{patch}' if patch else f'{major}.{minor}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path, nargs='+', help='path to a packaged Mach-O executable')
    for path in parser.parse_args().binary:
        try:
            minos = deployment_target(path)
            version = version_string(minos)
            if minos > MAX_MINOS:
                raise ValueError(f'macOS deployment target {version} exceeds 11.0')
        except (OSError, ValueError, struct.error) as error:
            parser.exit(1, f'{path}: {error}\n')
        print(f'{path}: thin arm64 Mach-O, MACOS, minos {version} (maximum 11.0)')


if __name__ == '__main__':
    main()

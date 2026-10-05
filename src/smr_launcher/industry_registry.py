"""Read the fixed industry-name table from the supported Railroads executable.

This module contains no game data. It extracts the table only when the complete
executable matches the reviewed SHA-256 and the requested addresses fall inside
unambiguous file-backed x86_64 Mach-O segments.
"""
from __future__ import annotations

import hashlib
import struct


SUPPORTED_GAME_SHA256 = "981ca7180759d8578babf54cd9a4f76b93db503b33a7f1938c6b1427b95680f0"
INDUSTRY_TABLE_ADDRESS = 0x1018CE910
INDUSTRY_TABLE_COUNT = 36
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_TEST_TABLE_COUNT = 4096
_UINT64_MAX = (1 << 64) - 1
_MH_MAGIC_64 = 0xFEEDFACF
_CPU_TYPE_X86_64 = 0x01000007
_LC_SEGMENT_64 = 0x19
_SEGMENT_COMMAND_64_SIZE = 72
_SECTION_64_SIZE = 80


class IndustryRegistryError(ValueError):
    """The executable or its industry table is outside the verified format."""


def extract_supported_industry_registry(data: bytes) -> tuple[str, ...]:
    """Extract the verified 36-entry table from the exact supported executable."""
    return _extract_registry(data, SUPPORTED_GAME_SHA256,
                             INDUSTRY_TABLE_ADDRESS, INDUSTRY_TABLE_COUNT)


def _extract_registry(data: bytes, expected_hash: str,
                      address: int, count: int) -> tuple[str, ...]:
    """Bounded low-level reader used by the fixed API and synthetic tests."""
    if not isinstance(data, bytes):
        raise IndustryRegistryError("Executable data must be bytes")
    if len(data) > MAX_EXECUTABLE_BYTES:
        raise IndustryRegistryError("Executable exceeds the supported audit size")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise IndustryRegistryError("Expected executable SHA-256 is invalid")
    if not isinstance(address, int) or isinstance(address, bool) or not (0 <= address <= _UINT64_MAX):
        raise IndustryRegistryError("Registry virtual address is invalid")
    if not isinstance(count, int) or isinstance(count, bool) or not (1 <= count <= MAX_TEST_TABLE_COUNT):
        raise IndustryRegistryError("Registry entry count exceeds the bounded limit")
    if address > _UINT64_MAX - count * 8:
        raise IndustryRegistryError("Registry pointer table address overflows")
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise IndustryRegistryError("Unsupported executable SHA-256; table offsets were not verified for this build")
    if len(data) < 32 or struct.unpack_from("<II", data) != (_MH_MAGIC_64, _CPU_TYPE_X86_64):
        raise IndustryRegistryError("Expected a thin little-endian x86_64 Mach-O")

    command_count, command_bytes = struct.unpack_from("<II", data, 16)
    if command_count > command_bytes // 8:
        raise IndustryRegistryError("Mach-O command count cannot fit in the declared load-command area")
    end = 32 + command_bytes
    if end > len(data):
        raise IndustryRegistryError("Truncated Mach-O load commands")

    offset = 32
    segments: list[tuple[int, int, int]] = []
    for _ in range(command_count):
        if offset + 8 > end:
            raise IndustryRegistryError("Truncated Mach-O command")
        command, size = struct.unpack_from("<II", data, offset)
        if size < 8 or size % 4 != 0 or offset + size > end:
            raise IndustryRegistryError("Invalid Mach-O command length")
        if command == _LC_SEGMENT_64:
            if size < _SEGMENT_COMMAND_64_SIZE:
                raise IndustryRegistryError("Truncated 64-bit segment")
            vm_address, virtual_size, file_offset, file_size = struct.unpack_from("<QQQQ", data, offset + 24)
            section_count = struct.unpack_from("<I", data, offset + 64)[0]
            if section_count > (size - _SEGMENT_COMMAND_64_SIZE) // _SECTION_64_SIZE:
                raise IndustryRegistryError("Invalid 64-bit segment section count")
            if (file_offset > len(data) or file_size > len(data) - file_offset
                    or file_size > virtual_size):
                raise IndustryRegistryError("Invalid file-backed segment")
            if vm_address > _UINT64_MAX - virtual_size:
                raise IndustryRegistryError("64-bit segment virtual range overflows")
            if file_size:
                segments.append((vm_address, file_offset, file_size))
        offset += size
    if offset != end:
        raise IndustryRegistryError("Mach-O load-command count does not consume the declared command area")

    # Reject overlapping file-backed virtual ranges up front. Otherwise a
    # malformed image could map the same requested address to multiple offsets.
    ordered = sorted(segments, key=lambda item: (item[0], item[2], item[1]))
    for previous, current in zip(ordered, ordered[1:]):
        previous_end = previous[0] + previous[2]
        if current[0] < previous_end:
            raise IndustryRegistryError("Overlapping file-backed segment mappings are ambiguous")
    segments = ordered

    def read_virtual(virtual_address: int, length: int) -> bytes:
        if length < 0 or virtual_address < 0 or virtual_address > _UINT64_MAX - length:
            raise IndustryRegistryError("Requested virtual range overflows")
        end_address = virtual_address + length
        matches = [(start, file_offset, file_size) for start, file_offset, file_size in segments
                   if start <= virtual_address and end_address <= start + file_size]
        if not matches:
            raise IndustryRegistryError("Registry address is outside file-backed segments")
        if len(matches) != 1:
            raise IndustryRegistryError("Overlapping file-backed segment mappings are ambiguous")
        start, file_offset, _file_size = matches[0]
        pos = file_offset + virtual_address - start
        return data[pos:pos + length]

    names = []
    for index in range(count):
        pointer = struct.unpack("<Q", read_virtual(address + index * 8, 8))[0]
        if pointer > _UINT64_MAX - 256:
            raise IndustryRegistryError("Registry string address overflows")
        raw = bytearray()
        for char_offset in range(256):
            byte = read_virtual(pointer + char_offset, 1)[0]
            if byte == 0:
                break
            if byte < 32 or byte > 126:
                raise IndustryRegistryError("Registry identity contains unexpected bytes")
            raw.append(byte)
        else:
            raise IndustryRegistryError("Unterminated registry identity")
        if not raw:
            raise IndustryRegistryError("Empty registry identity")
        names.append(raw.decode("ascii"))
    if len(set(names)) != count:
        raise IndustryRegistryError("Duplicate registry identities")
    return tuple(names)


__all__ = [
    "SUPPORTED_GAME_SHA256",
    "INDUSTRY_TABLE_ADDRESS",
    "INDUSTRY_TABLE_COUNT",
    "IndustryRegistryError",
    "extract_supported_industry_registry",
]

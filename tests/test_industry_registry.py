"""Runtime Mach-O registry reader tests use synthetic binaries only."""
import hashlib
import struct
import unittest

from smr_launcher import industry_registry


def synthetic_binary(*, overlapping=False):
    data = bytearray(2048 if overlapping else 1024)
    commands = 2 if overlapping else 1
    command_bytes = commands * 72
    struct.pack_into("<IIIIIIII", data, 0,
                     0xFEEDFACF, 0x01000007, 3, 2, commands, command_bytes, 0, 0)
    first_file_offset = 1024 if overlapping else 0
    struct.pack_into("<II16sQQQQIIII", data, 32,
                     0x19, 72, b"__TEXT", 0x1000, 0x400,
                     first_file_offset, 0x400, 7, 5, 0, 0)
    if overlapping:
        struct.pack_into("<II16sQQQQIIII", data, 104,
                         0x19, 72, b"__OVERLAP", 0x1080, 0x100,
                         1536, 0x100, 7, 5, 0, 0)
        pointer_table_offset = first_file_offset + 0x80
        string_a_offset, string_b_offset = first_file_offset + 0xC0, first_file_offset + 0xD0
    else:
        pointer_table_offset = 0x80
        string_a_offset, string_b_offset = 0xC0, 0xD0
    struct.pack_into("<QQ", data, pointer_table_offset, 0x10C0, 0x10D0)
    data[string_a_offset:string_a_offset + 9] = b"FactoryA\0"
    data[string_b_offset:string_b_offset + 9] = b"FactoryB\0"
    return bytearray(data)


def low_level(data, address=0x1080, count=2):
    raw = bytes(data)
    return industry_registry._extract_registry(
        raw, hashlib.sha256(raw).hexdigest(), address, count)


class RuntimeIndustryRegistryTests(unittest.TestCase):
    def test_public_api_is_fixed_to_the_supported_build_and_36_names(self):
        self.assertEqual(industry_registry.INDUSTRY_TABLE_COUNT, 36)
        self.assertEqual(industry_registry.INDUSTRY_TABLE_ADDRESS, 0x1018CE910)
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "Unsupported executable SHA-256"):
            industry_registry.extract_supported_industry_registry(bytes(synthetic_binary()))

    def test_private_reader_extracts_only_bounded_file_backed_names(self):
        self.assertEqual(low_level(synthetic_binary()), ("FactoryA", "FactoryB"))
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "outside file-backed"):
            low_level(synthetic_binary(), 0x13FC, 2)
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "bounded limit"):
            low_level(synthetic_binary(), count=4097)

    def test_load_command_count_must_consume_exact_declared_area(self):
        data = synthetic_binary()
        struct.pack_into("<I", data, 16, 2)
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "Truncated Mach-O command"):
            low_level(data)

        data = synthetic_binary()
        struct.pack_into("<I", data, 20, 80)
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "does not consume"):
            low_level(data)

    def test_overlapping_segment_virtual_mappings_are_rejected(self):
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "Overlapping file-backed"):
            low_level(synthetic_binary(overlapping=True))

    def test_segment_file_bounds_section_count_and_virtual_overflow(self):
        data = synthetic_binary()
        struct.pack_into("<Q", data, 32 + 40, len(data) + 1)
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "Invalid file-backed segment"):
            low_level(data)

        data = synthetic_binary()
        struct.pack_into("<I", data, 32 + 64, 1)
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "section count"):
            low_level(data)

        data = synthetic_binary()
        struct.pack_into("<Q", data, 32 + 24, (1 << 64) - 10)
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "virtual range overflows"):
            low_level(data)

    def test_duplicate_empty_nonascii_and_unterminated_names_fail_closed(self):
        data = synthetic_binary()
        struct.pack_into("<Q", data, 136, 0x10C0)
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "Duplicate"):
            low_level(data)

        data = synthetic_binary()
        data[192] = 0
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "Empty"):
            low_level(data)

        data = synthetic_binary()
        data[192] = 1
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "unexpected bytes"):
            low_level(data)

        data = synthetic_binary()
        data[192:448] = b"A" * 256
        with self.assertRaisesRegex(industry_registry.IndustryRegistryError, "Unterminated"):
            low_level(data)


if __name__ == "__main__":
    unittest.main()

"""Invented byte fixtures; no licensed model or converter-generated oracle."""

import struct
import unittest
from unittest.mock import patch

from smr_launcher import bank_model


def synthetic_model(*, modern=False, colors=False, reverse_properties=False):
    """Encode a literal six/seven-block specification independently of the writer."""
    parts = []
    offsets = {}

    def raw(value, label=None):
        if label:
            offsets[label] = sum(map(len, parts))
        parts.append(value)

    def number(fmt, value, label=None):
        raw(struct.pack("<" + fmt, value), label)

    def string(value, label=None):
        number("I", len(value), label)
        raw(value)

    def links(values, label=None):
        number("I", len(values), label)
        for value in values:
            number("i", value)

    def block(kind):
        if modern:
            number("I", 0)
        else:
            string(kind, kind.decode() + ".class")

    def net(name):
        string(name)
        number("I" if modern else "i", 0 if modern else -1, name.decode() + ".extra")
        number("i", -1, name.decode() + ".controller")

    # Raw float words include negative zero, a subnormal and distinct NaN payloads.
    # Preservation is bitwise; no float arithmetic or plausibility claim is involved.
    words = (0x80000000, 0x00000001, 0x7FC01234, 0x3F800000, 0xFFC05678)

    def payload(count, seed):
        return b"".join(struct.pack("<I", words[(i + seed) % len(words)]) for i in range(count))

    classes = [
        b"NiNode",
        b"NiTriStrips",
        b"NiTexturingProperty",
        b"NiSourceTexture",
        b"NiMaterialProperty",
        b"NiTriStripsData",
    ]
    version = "10.0.1.0" if modern else "4.2.2.0"
    raw(f"NetImmerse File Format, Version {version}\n".encode())
    number("I", 0x0A000100 if modern else 0x04020200, "version")
    number("I", 7 if modern else 6, "blocks")
    if modern:
        number("H", 7)
        for kind in [classes[0], b"NiCollisionData", *classes[1:]]:
            string(kind)
        raw(struct.pack("<7H", 0, 1, 2, 3, 4, 5, 6))
        number("I", 0)

    def av(name, flags, properties, collision, seed):
        net(name)
        number("H", flags, name.decode() + ".flags")
        raw(payload(13, seed), name.decode() + ".transform")
        if not modern:
            raw(bytes(12), name.decode() + ".velocity")
        links(properties, name.decode() + ".properties")
        number("i" if modern else "B", collision if modern else 0, name.decode() + ".collision")

    block(classes[0])
    av(b"invented-root", 0x13 if modern else 0x9D, (), 1, 0)
    links((2,) if modern else (1,), "children")
    links((), "effects")
    if modern:
        block(b"NiCollisionData")
        number("i", 0)
        number("I", 6)
        number("B", 0)
    block(classes[1])
    properties = (3, 5) if modern else (2, 4)
    av(
        b"invented-mesh",
        0x0A if modern else 0x54,
        properties[::-1] if reverse_properties else properties,
        -1,
        1,
    )
    number("i", 6 if modern else 5, "geometry")
    number("i", -1, "skin")
    if modern:
        number("B", 0)
    block(classes[2])
    net(b"invented-texturing")
    number("H", 0x1234)
    number("I", 3, "apply_mode")
    number("I", 7, "slots")
    number("B", 1, "base_texture")
    number("i", 4 if modern else 3, "texture_source")
    raw(struct.pack("<IIIhh", 2, 4, 0, -3, 11), "descriptor")
    raw(bytes(6), "extra_slots")
    if modern:
        number("I", 0)
    block(classes[3])
    net(b"invented-source")
    number("B", 1, "external")
    string(b"synthetic/checker.dds", "filename")
    raw(struct.pack("<IIIB", 5, 2, 3, 1), "texture_format")
    block(classes[4])
    net(b"invented-material")
    number("H", 0x4321)
    raw(payload(14, 2), "material")
    block(classes[5])
    number("H", 4, "vertices_count")
    number("B", 1, "has_vertices")
    raw(payload(12, 3), "vertices")
    if modern:
        number("H", 1)
    number("B", 1, "has_normals")
    raw(payload(12, 4), "normals")
    raw(payload(4, 0), "bound")
    number("B", int(colors), "has_colors")
    if colors:
        raw(payload(16, 1), "colors")
    if not modern:
        number("H", 1, "uv_flags")
    raw(payload(8, 2), "uvs")
    if modern:
        number("H", 0)
    number("H", 2, "triangles")
    number("H", 1, "strips")
    number("H", 4, "strip_length")
    raw(struct.pack("<4H", 3, 1, 0, 2), "indices")
    links((0,), "roots")
    return b"".join(parts), offsets


class BankModelTests(unittest.TestCase):
    def test_roundtrip_and_complete_target_bytes(self):
        for colors in (False, True):
            for reverse in (False, True):
                with self.subTest(colors=colors, reverse=reverse):
                    source, _ = synthetic_model(colors=colors, reverse_properties=reverse)
                    expected, _ = synthetic_model(
                        modern=True, colors=colors, reverse_properties=reverse
                    )
                    graph = bank_model._parse_legacy(source)
                    self.assertEqual(bank_model._serialize(graph), source)
                    self.assertEqual(bank_model._convert_supported_layout(source), expected)

    def test_changed_float_bits_survive_conversion(self):
        source, offsets = synthetic_model(colors=True)
        expected, target_offsets = synthetic_model(modern=True, colors=True)
        for field in [
            "invented-root.transform",
            "invented-mesh.transform",
            "material",
            "vertices",
            "normals",
            "bound",
            "colors",
            "uvs",
        ]:
            with self.subTest(field=field):
                changed = bytearray(source)
                changed[offsets[field]] ^= 0x20
                wanted = bytearray(expected)
                wanted[target_offsets[field]] ^= 0x20
                self.assertEqual(
                    bank_model._convert_supported_layout(bytes(changed)), bytes(wanted)
                )

    def test_every_truncation_and_trailing_bytes_refused(self):
        source, _ = synthetic_model()
        for size in range(len(source)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                bank_model._convert_supported_layout(source[:size])
        with self.assertRaisesRegex(ValueError, "trailing bytes"):
            bank_model._convert_supported_layout(source + b"\0")

    def test_unsupported_fields_and_graphs_refused(self):
        source, offsets = synthetic_model()
        mutations = [
            ("version", "I", 0x04020201),
            ("blocks", "I", 7),
            ("invented-root.extra", "i", 0),
            ("invented-root.controller", "i", 0),
            ("invented-root.flags", "H", 0x19D),
            ("invented-root.flags", "H", 0x95),
            ("invented-mesh.flags", "H", 0x5C),
            ("invented-root.velocity", "B", 1),
            ("invented-root.properties", "I", 1),
            ("invented-root.collision", "B", 1),
            ("children", "I", 2),
            ("effects", "I", 1),
            ("invented-mesh.properties", "I", 3),
            ("geometry", "i", 4),
            ("skin", "i", 0),
            ("apply_mode", "I", 5),
            ("slots", "I", 8),
            ("base_texture", "B", 0),
            ("texture_source", "i", 2),
            ("descriptor", "I", 4),
            ("extra_slots", "B", 1),
            ("external", "B", 0),
            ("filename", "I", 4097),
            ("texture_format", "I", 7),
            ("vertices_count", "H", 4097),
            ("vertices_count", "H", 2),
            ("has_vertices", "B", 0),
            ("has_normals", "B", 0),
            ("has_colors", "B", 2),
            ("uv_flags", "H", 2),
            ("triangles", "H", 0),
            ("triangles", "H", 8193),
            ("strips", "H", 0),
            ("strips", "H", 257),
            ("strip_length", "H", 2),
            ("strip_length", "H", 5),
            ("indices", "H", 4),
            ("roots", "I", 0),
        ]
        for label, fmt, value in mutations:
            with self.subTest(field=label, value=value):
                changed = bytearray(source)
                struct.pack_into("<" + fmt, changed, offsets[label], value)
                with self.assertRaises(ValueError):
                    bank_model._convert_supported_layout(bytes(changed))
        # A same-width class substitution and duplicate numeric property identity.
        changed = bytearray(source)
        changed[offsets["NiNode.class"] + 4] = ord("X")
        with self.assertRaisesRegex(ValueError, "class/order"):
            bank_model._convert_supported_layout(bytes(changed))
        changed = bytearray(source)
        struct.pack_into("<i", changed, offsets["invented-mesh.properties"] + 8, 2)
        with self.assertRaisesRegex(ValueError, "property identities"):
            bank_model._convert_supported_layout(bytes(changed))

    def test_input_bounds_and_immutable_bytes(self):
        source, _ = synthetic_model()
        for value in [bytearray(source), memoryview(source), b"x" * 131073]:
            with self.subTest(kind=type(value)), self.assertRaises(ValueError):
                bank_model._convert_supported_layout(value)

    def test_public_operation_refuses_unsealed_source(self):
        for value in [b"", bytearray(), bytes(bank_model.SOURCE_SIZE)]:
            with self.subTest(kind=type(value)), self.assertRaises(ValueError):
                bank_model.convert_legacy_bank(value)

    def test_public_operation_holds_on_wrong_output(self):
        # Fault injection tests output seals without pretending synthetic bytes
        # are an admitted licensed source. No test changes production constants.
        source = bytes(bank_model.SOURCE_SIZE)
        with patch.object(bank_model.hashlib, "sha256") as digest:
            digest.return_value.hexdigest.return_value = bank_model.SOURCE_SHA256
            with patch.object(bank_model, "_convert_supported_layout", return_value=b"bad"):
                with self.assertRaisesRegex(ValueError, "HOLD: bank output size mismatch"):
                    bank_model.convert_legacy_bank(source)
            with patch.object(
                bank_model, "_convert_supported_layout", return_value=bytes(bank_model.OUTPUT_SIZE)
            ):
                with self.assertRaisesRegex(ValueError, "HOLD: bank output digest mismatch"):
                    bank_model.convert_legacy_bank(source)

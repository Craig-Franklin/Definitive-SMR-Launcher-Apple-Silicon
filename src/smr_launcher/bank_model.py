"""Exact, dependency-free legacy bank migration.

The public operation admits one reviewed asset digest. Private helpers support
small invented graphs with the same six-block layout for regression tests; they
are not a general NIF upgrader. Callers own build applicability, prepared-copy
transactions, and independent licensed-asset/runtime acceptance.
"""

import hashlib
import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import Tuple


SOURCE_SHA256 = "b81023bd501b4897cc3c08fc96c2ee69df5149d741118968375cb95a502be25c"
OUTPUT_SHA256 = "9b68dbb2ae09c813ff9b8547b1f7a9cdf60178d9a5d6fb90a791adbabc200547"
SOURCE_SIZE = 11642
OUTPUT_SIZE = 11708
SOURCE_VERSION = 0x04020200
OUTPUT_VERSION = 0x0A000100
_SOURCE_HEADER = b"NetImmerse File Format, Version 4.2.2.0\n"
_OUTPUT_HEADER = b"NetImmerse File Format, Version 10.0.1.0\n"
_CLASSES = (
    b"NiNode",
    b"NiTriStrips",
    b"NiTexturingProperty",
    b"NiSourceTexture",
    b"NiMaterialProperty",
    b"NiTriStripsData",
)
_TARGET_CLASSES = (_CLASSES[0], b"NiCollisionData") + _CLASSES[1:]
_MAX_BYTES = 131072
_MAX_STRING = 4096
_MAX_VERTICES = 4096
_MAX_TRIANGLES = 8192
_MAX_STRIPS = 256


def _require(condition, message):
    if not condition:
        raise ValueError(message)


class _Reader:
    def __init__(self, data):
        _require(
            type(data) is bytes and len(data) <= _MAX_BYTES, "expected bounded immutable bytes"
        )
        self.data = data
        self.pos = 0

    def raw(self, size):
        _require(0 <= size <= len(self.data) - self.pos, "truncated bank model")
        result = self.data[self.pos : self.pos + size]
        self.pos += size
        return result

    def number(self, fmt):
        return struct.unpack("<" + fmt, self.raw(struct.calcsize("<" + fmt)))[0]

    def count(self, fmt, maximum):
        value = self.number(fmt)
        _require(value <= maximum, "unsupported count")
        return value

    def string(self):
        value = self.raw(self.count("I", _MAX_STRING))
        _require(all(byte < 128 for byte in value), "non-ASCII string")
        return value

    def links(self, maximum):
        return tuple(self.number("i") for _ in range(self.count("I", maximum)))


class _Writer:
    def __init__(self):
        self.parts = []
        self.size = 0

    def raw(self, value):
        self.size += len(value)
        _require(self.size <= _MAX_BYTES, "serialized model exceeds bound")
        self.parts.append(value)

    def number(self, fmt, value):
        self.raw(struct.pack("<" + fmt, value))

    def string(self, value):
        self.number("I", len(value))
        self.raw(value)

    def links(self, values):
        self.number("I", len(values))
        for value in values:
            self.number("i", value)

    def finish(self):
        return b"".join(self.parts)


@dataclass(frozen=True)
class _Net:
    name: bytes


@dataclass(frozen=True)
class _AV:
    net: _Net
    flags: int
    transform: bytes  # Translation, matrix, scale: 13 raw float32 values.
    properties: Tuple[int, ...]


@dataclass(frozen=True)
class _Texture:
    net: _Net
    flags: int
    apply_mode: int
    source: int
    clamp: int
    filter: int
    uv_set: int
    ps2_l: int
    ps2_k: int


@dataclass(frozen=True)
class _SourceTexture:
    net: _Net
    filename: bytes
    pixel_layout: int
    mipmaps: int
    alpha_format: int
    static: int


@dataclass(frozen=True)
class _Material:
    net: _Net
    flags: int
    payload: bytes  # Four colors, glossiness, alpha: 14 raw float32 values.


@dataclass(frozen=True)
class _Geometry:
    count: int
    vertices: bytes
    normals: bytes
    bound: bytes
    colors: bytes
    uvs: bytes
    triangles: int
    lengths: Tuple[int, ...]
    indices: Tuple[int, ...]


@dataclass(frozen=True)
class _Graph:
    root: _AV
    child: _AV
    texture: _Texture
    source_texture: _SourceTexture
    material: _Material
    geometry: _Geometry


def _read_net(r):
    name = r.string()
    _require(r.number("i") == -1, "extra-data chains unsupported")
    _require(r.number("i") == -1, "controllers unsupported")
    return _Net(name)


def _migrate_flags(old):
    # High legacy bits are outside this supported layout. Do not silently lose
    # bits through either the specified 16-bit wrap or the six-bit target mask.
    _require(0 <= old <= 0xFF, "unsupported AV flag fields")
    migrated = ((old & 255) + ((old & 0xFF00) * 2)) & 0xFFFF
    return ((migrated & 1) + ((migrated >> 4) * 2)) & 0x3F, (migrated >> 1) & 7


def _read_av(r, propagation):
    net = _read_net(r)
    flags = r.number("H")
    _require(_migrate_flags(flags)[1] == propagation, "unsupported collision propagation")
    transform = r.raw(52)
    _require(r.raw(12) == bytes(12), "velocity unsupported")
    properties = r.links(2)
    _require(r.number("B") == 0, "bounding volumes unsupported")
    return _AV(net, flags, transform, properties)


def _parse_legacy(data):
    """Read only the bounded reviewed layout, retaining raw payload bits."""
    r = _Reader(data)
    _require(r.raw(len(_SOURCE_HEADER)) == _SOURCE_HEADER, "wrong source header")
    _require(r.number("I") == SOURCE_VERSION, "wrong source version")
    _require(r.number("I") == 6, "wrong block count")

    def kind(index):
        _require(r.string() == _CLASSES[index], "wrong block class/order")

    kind(0)
    root = _read_av(r, 6)
    _require(root.properties == (), "root properties unsupported")
    _require(r.links(1) == (1,), "wrong child identity")
    _require(r.links(0) == (), "effects unsupported")
    kind(1)
    child = _read_av(r, 2)
    _require(
        len(child.properties) == 2 and set(child.properties) == {2, 4}, "wrong property identities"
    )
    _require(r.number("i") == 5, "wrong geometry identity")
    _require(r.number("i") == -1, "skin unsupported")
    kind(2)
    net = _read_net(r)
    flags = r.number("H")
    mode = r.number("I")
    _require(mode <= 4, "unsupported texture apply mode")
    _require(r.number("I") == 7, "unsupported texture slot count")
    _require(r.number("B") == 1, "base texture required")
    source = r.number("i")
    _require(source == 3, "wrong texture source identity")
    clamp, filtering, uv_set = (r.number("I") for _ in range(3))
    _require(clamp <= 3 and filtering <= 5 and uv_set == 0, "unsupported texture descriptor")
    ps2_l, ps2_k = r.number("h"), r.number("h")
    _require(r.raw(6) == bytes(6), "additional texture slots unsupported")
    texture = _Texture(net, flags, mode, source, clamp, filtering, uv_set, ps2_l, ps2_k)
    kind(3)
    net = _read_net(r)
    _require(r.number("B") == 1, "embedded textures unsupported")
    filename = r.string()
    pixel, mipmaps, alpha = (r.number("I") for _ in range(3))
    _require(pixel <= 6 and mipmaps <= 2 and alpha <= 3, "unsupported texture format")
    static = r.number("B")
    _require(static in (0, 1), "unsupported static flag")
    source_texture = _SourceTexture(net, filename, pixel, mipmaps, alpha, static)
    kind(4)
    material = _Material(_read_net(r), r.number("H"), r.raw(56))
    kind(5)
    count = r.count("H", _MAX_VERTICES)
    _require(count >= 3, "insufficient vertices")
    _require(r.number("B") == 1, "vertices required")
    vertices = r.raw(count * 12)
    _require(r.number("B") == 1, "normals required")
    normals, bound = r.raw(count * 12), r.raw(16)
    has_colors = r.number("B")
    _require(has_colors in (0, 1), "unsupported color flag")
    colors = r.raw(count * 16) if has_colors else b""
    _require(r.number("H") == 1, "unsupported UV/vector flags")
    uvs = r.raw(count * 8)
    triangles = r.count("H", _MAX_TRIANGLES)
    strips = r.count("H", _MAX_STRIPS)
    _require(triangles > 0 and strips > 0, "empty topology unsupported")
    lengths = tuple(r.number("H") for _ in range(strips))
    _require(all(length >= 3 for length in lengths), "invalid strip length")
    _require(sum(lengths) == triangles + 2 * strips, "inconsistent strip topology")
    indices = tuple(r.number("H") for _ in range(sum(lengths)))
    _require(all(index < count for index in indices), "vertex index out of range")
    geometry = _Geometry(count, vertices, normals, bound, colors, uvs, triangles, lengths, indices)
    _require(r.links(1) == (0,), "wrong end roots")
    _require(r.pos == len(data), "trailing bytes")
    return _Graph(root, child, texture, source_texture, material, geometry)


def _write_net(w, net, modern):
    w.string(net.name)
    w.number("I" if modern else "i", 0 if modern else -1)
    w.number("i", -1)


def _remap(index):
    # Collision insertion is at numeric index 1; class names never resolve refs.
    return index + 1 if index >= 1 else index


def _write_av(w, av, modern, collision):
    _write_net(w, av.net, modern)
    w.number("H", _migrate_flags(av.flags)[0] if modern else av.flags)
    w.raw(av.transform)
    if not modern:
        w.raw(bytes(12))
    w.links(tuple(_remap(i) for i in av.properties) if modern else av.properties)
    w.number("i" if modern else "B", collision if modern else 0)


def _serialize(graph, modern=False):
    """Serialize an internal graph obtained from the validated source reader."""
    w = _Writer()
    w.raw(_OUTPUT_HEADER if modern else _SOURCE_HEADER)
    w.number("I", OUTPUT_VERSION if modern else SOURCE_VERSION)
    classes = _TARGET_CLASSES if modern else _CLASSES
    w.number("I", len(classes))
    if modern:
        w.number("H", len(classes))
        for name in classes:
            w.string(name)
        for index in range(len(classes)):
            w.number("H", index)
        w.number("I", 0)  # Object groups.

    def block(name):
        if modern:
            w.number("I", 0)  # NiObject tag.
        else:
            w.string(name)

    block(_CLASSES[0])
    _write_av(w, graph.root, modern, 1)
    w.links((2,) if modern else (1,))
    w.links(())
    if modern:
        block(b"NiCollisionData")
        w.number("i", 0)  # Exact root backlink.
        w.number("I", _migrate_flags(graph.root.flags)[1])
        w.number("B", 0)  # No ABV; collision mode 3 is implicit, not serialized.
    block(_CLASSES[1])
    _write_av(w, graph.child, modern, -1)
    w.number("i", 6 if modern else 5)
    w.number("i", -1)
    if modern:
        w.number("B", 0)  # No shader.
    block(_CLASSES[2])
    t = graph.texture
    _write_net(w, t.net, modern)
    w.number("H", t.flags)
    w.number("I", t.apply_mode)
    w.number("I", 7)
    w.number("B", 1)
    w.number("i", _remap(t.source) if modern else t.source)
    for value in (t.clamp, t.filter, t.uv_set):
        w.number("I", value)
    w.number("h", t.ps2_l)
    w.number("h", t.ps2_k)
    w.raw(bytes(6))
    if modern:
        w.number("I", 0)  # Shader texture count.
    block(_CLASSES[3])
    t = graph.source_texture
    _write_net(w, t.net, modern)
    w.number("B", 1)
    w.string(t.filename)
    for value in (t.pixel_layout, t.mipmaps, t.alpha_format):
        w.number("I", value)
    w.number("B", t.static)
    block(_CLASSES[4])
    _write_net(w, graph.material.net, modern)
    w.number("H", graph.material.flags)
    w.raw(graph.material.payload)
    block(_CLASSES[5])
    g = graph.geometry
    w.number("H", g.count)
    w.number("B", 1)
    w.raw(g.vertices)
    if modern:
        w.number("H", 1)  # One UV set, zero extra-vector bits.
    w.number("B", 1)
    w.raw(g.normals)
    w.raw(g.bound)
    w.number("B", int(bool(g.colors)))
    w.raw(g.colors)
    if not modern:
        w.number("H", 1)
    w.raw(g.uvs)
    if modern:
        w.number("H", 0)  # Consistency flags.
    w.number("H", g.triangles)
    w.number("H", len(g.lengths))
    for value in g.lengths + g.indices:
        w.number("H", value)
    w.links((0,))
    return w.finish()


def _convert_supported_layout(data):
    graph = _parse_legacy(data)
    _require(_serialize(graph) == data, "source roundtrip mismatch")
    return _serialize(graph, modern=True)


def convert_legacy_bank(data: bytes) -> bytes:
    """Return the exact reviewed conversion or raise ValueError without effects.

    Acceptance here is a byte-level seal, not rendering/gameplay verification.
    A mismatched output holds preparation; there is no template or fallback.
    """
    _require(type(data) is bytes, "expected immutable bytes")
    _require(len(data) == SOURCE_SIZE, "unsupported bank source size")
    _require(hashlib.sha256(data).hexdigest() == SOURCE_SHA256, "unsupported bank source digest")
    return _convert_reviewed_bank(
        data,
        (SOURCE_SHA256, SOURCE_SIZE, SOURCE_VERSION),
        (OUTPUT_SHA256, OUTPUT_SIZE, OUTPUT_VERSION),
    )


@lru_cache(maxsize=8)
def _convert_reviewed_bank(data, source_contract, output_contract):
    """Cache successful immutable bytes by both complete byte contracts.

    Input admission remains in the public function. Recipe context, provider and
    timestamp applicability remains with callers and is never cached here.
    """
    expected_digest, expected_size, _version = output_contract
    output = _convert_supported_layout(data)
    _require(len(output) == expected_size, "HOLD: bank output size mismatch")
    _require(
        hashlib.sha256(output).hexdigest() == expected_digest, "HOLD: bank output digest mismatch"
    )
    return output

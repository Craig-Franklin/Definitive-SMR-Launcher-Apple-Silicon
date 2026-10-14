"""Native image correspondence checks using original synthetic PNGs only."""

import copy
import hashlib
import json
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/research"))
from image_correspondence import point_for_frame


class ImageCorrespondenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.frame = dict(
            sha256="b" * 64,
            pixel_width=24,
            pixel_height=20,
            scale=0.5,
            window=dict(width=48, height=40),
        )
        self.result = dict(
            schema="smr-image-control-correspondence/1",
            reference_sha256="a" * 64,
            input_sha256="b" * 64,
            width=24,
            height=20,
            rectangle=[10, 8, 8, 8],
            point=[11.5, 10],
            claim="image_correspondence_only",
            runtime_authority_granted=False,
        )

    def point(self, result=None, **kwargs):
        return point_for_frame(
            self.result if result is None else result,
            reference_sha256="a" * 64,
            frame=kwargs.get("frame", self.frame),
            rectangle=kwargs.get("rectangle", [2, 3, 8, 8]),
            offset=kwargs.get("offset", [1.5, 2]),
        )

    def test_current_match_and_actual_scale_determine_point(self):
        self.assertEqual(self.point(), (23, 20))
        moved = dict(self.result, rectangle=[4, 1, 8, 8], point=[5.5, 3])
        self.assertEqual(self.point(moved), (11, 6))

    def test_closed_projection_identity_and_literal_types_are_required(self):
        for key, replacement in (
            ("schema", "other"),
            ("reference_sha256", "c" * 64),
            ("input_sha256", "c" * 64),
            ("width", True),
            ("height", 19),
            ("claim", "control_identified"),
            ("runtime_authority_granted", 0),
            ("rectangle", [True, 8, 8, 8]),
            ("rectangle", [10, 8, 7, 8]),
            ("rectangle", [20, 8, 8, 8]),
            ("point", [10, 8]),
            ("point", [float("nan"), 10]),
        ):
            with self.subTest(key=key, replacement=replacement), self.assertRaises(ValueError):
                self.point(dict(self.result, **{key: replacement}))
        with self.assertRaises(ValueError):
            self.point(dict(self.result, extra="unreviewed"))

    def test_reference_offsets_and_window_geometry_cannot_supply_a_fallback(self):
        for rectangle, offset in (
            ([-1, 3, 8, 8], [1.5, 2]),
            ([2, 3, 8, 8], [8, 2]),
            ([2, 3, 8, 8], [float("inf"), 2]),
        ):
            with self.subTest(rectangle=rectangle, offset=offset), self.assertRaises(ValueError):
                self.point(rectangle=rectangle, offset=offset)
        for key, replacement in (
            ("scale", 0),
            ("scale", True),
            ("scale", float("inf")),
            ("pixel_width", True),
            ("sha256", "c" * 64),
            ("window", dict(width=80, height=40)),
        ):
            frame = copy.deepcopy(self.frame)
            frame[key] = replacement
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.point(frame=frame)


@unittest.skipUnless(
    sys.platform == "darwin" and platform.machine() == "arm64", "Apple Silicon helper"
)
class ImageControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.build.cleanup)
        directory = Path(cls.build.name).resolve()
        cls.binary = directory / "image-control-match"
        sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
        source = ROOT / "scripts/research/image_control"
        subprocess.run(
            [
                "swiftc",
                "-parse-as-library",
                "-swift-version",
                "6",
                "-warnings-as-errors",
                "-O",
                "-target",
                "arm64-apple-macosx15.0",
                "-sdk",
                sdk,
                "-module-cache-path",
                str(directory / "cache"),
                str(source / "owned_image.swift"),
                str(source / "match_control.swift"),
                "-o",
                str(cls.binary),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.reference = self.directory / "synthetic reference.png"
        self.target = self.directory / "synthetic target.png"
        self.pattern = [
            [bytes(((x * 19 + y * 7) % 256, (x * 3 + y * 29) % 256, 201, 255)) for x in range(8)]
            for y in range(8)
        ]
        self.write_image(self.reference, [(2, 3)])
        self.write_image(self.target, [(10, 8)])

    def write_image(self, path, positions, *, width=24, height=20, pattern=None):
        pixels = [[bytes((13, 17, 29, 255)) for _ in range(width)] for _ in range(height)]
        for x, y in positions:
            for dy, row in enumerate(self.pattern if pattern is None else pattern):
                for dx, pixel in enumerate(row):
                    pixels[y + dy][x + dx] = pixel

        def chunk(kind, value):
            return (
                struct.pack(">I", len(value))
                + kind
                + value
                + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
            )

        rows = b"".join(b"\0" + b"".join(row) for row in pixels)
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b"")
        )

    def run_match(
        self, *, rectangle=(2, 3, 8, 8), offset=(1.5, 2), reference=None, target_hash=None
    ):
        reference = self.reference if reference is None else reference
        return subprocess.run(
            [
                str(self.binary),
                str(reference),
                hashlib.sha256(reference.read_bytes()).hexdigest(),
                str(self.target),
                target_hash or hashlib.sha256(self.target.read_bytes()).hexdigest(),
                *map(str, rectangle),
                *map(str, offset),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def refusal(self, result, code):
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "smr-image-control-correspondence/1: " + code + "\n")

    def test_unique_match_uses_fresh_top_left_pixels_and_declared_relative_point(self):
        result = self.run_match()
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["rectangle"], [10, 8, 8, 8])
        self.assertEqual(value["point"], [11.5, 10])
        self.assertEqual(value["width"], 24)
        self.assertEqual(value["height"], 20)
        self.assertEqual(
            value["input_sha256"], hashlib.sha256(self.target.read_bytes()).hexdigest()
        )
        self.assertEqual(
            value["reference_sha256"], hashlib.sha256(self.reference.read_bytes()).hexdigest()
        )
        self.assertEqual(value["claim"], "image_correspondence_only")
        self.assertFalse(value["runtime_authority_granted"])
        self.assertNotIn(str(self.directory), result.stdout)

    def test_duplicate_matches_refuse_instead_of_selecting_one(self):
        self.write_image(self.target, [(1, 1), (14, 10)])
        self.refusal(self.run_match(), "match_ambiguous")

    def test_missing_and_changed_control_pixels_refuse_without_fuzzy_matching(self):
        self.write_image(self.target, [])
        self.refusal(self.run_match(), "match_missing")
        changed = [list(row) for row in self.pattern]
        changed[4][4] = bytes((0, 0, 0, 255))
        self.write_image(self.target, [(10, 8)], pattern=changed)
        self.refusal(self.run_match(), "match_missing")

    def test_changed_canvas_refuses_without_scaling(self):
        self.write_image(self.target, [(10, 8)], width=25)
        self.refusal(self.run_match(), "geometry_changed")

    def test_flat_reference_refuses_even_if_shape_would_fit_once(self):
        self.write_image(self.reference, [])
        self.refusal(self.run_match(), "reference_invalid")

    def test_invalid_reference_and_relative_point_refuse(self):
        for rectangle, offset in (
            ((-1, 3, 8, 8), (1, 2)),
            ((20, 3, 8, 8), (1, 2)),
            ((2, 3, 3, 8), (1, 2)),
            ((2, 3, 8, 8), (-0.1, 2)),
            ((2, 3, 8, 8), (8, 2)),
            ((2, 3, 8, 8), ("nan", 2)),
            ((2, 3, 8, 8), (1, "inf")),
        ):
            with self.subTest(rectangle=rectangle, offset=offset):
                self.refusal(
                    self.run_match(rectangle=rectangle, offset=offset), "reference_invalid"
                )

    def test_hash_mismatch_and_symlink_refuse_without_private_path_output(self):
        self.refusal(self.run_match(target_hash="0" * 64), "hash_mismatch")
        link = self.directory / "linked.png"
        link.symlink_to(self.reference)
        self.refusal(self.run_match(reference=link), "input_invalid")

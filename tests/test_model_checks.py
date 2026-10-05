"""Synthetic legacy model headers: risk detection must not imply safe conversion."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from smr_launcher.model_checks import nif_header, inspect_models
from smr_launcher.import_checks import inspect_map


def header(version, prefix="NetImmerse"):
    return (f"{prefix} File Format, Version {'.'.join(map(str, version))}\n".encode()
            + int.from_bytes(bytes(version), "big").to_bytes(4, "little"))


class ModelChecksTests(unittest.TestCase):
    def test_legacy_boundary_uses_numeric_version_and_consistent_binary(self):
        for version, risk in [((4, 2, 2, 0), True), ((5, 0, 0, 18), True),
                              ((5, 0, 0, 19), False), ((20, 0, 0, 4), False)]:
            self.assertEqual(nif_header(header(version))["legacy_collision_path"], risk)
        self.assertEqual(nif_header(header((20, 0, 0, 4), "Gamebryo"))["status"], "recognized")
        self.assertEqual(nif_header(header((4, 2, 2, 0))[:-1])["status"], "inconsistent")
        self.assertEqual(nif_header(header((4, 2, 2, 0))[:-4]+b"\0"*4)["status"], "inconsistent")

    def test_malformed_unsupported_or_unbounded_headers_never_pass(self):
        for data in [b"", b"x"*300+header((4, 2, 2, 0)),
                     b"NetImmerse File Format, Version 4.999.2.0\n\0\0\0\0",
                     b"NetImmerse File Format, Version 3.1\n",
                     header((3, 1, 0, 0)), header((4, 2, 2, 0)).replace(b"\n", b" ")]:
            self.assertEqual(nif_header(data)["status"], "unrecognized")

    def test_import_warns_preserves_bytes_and_reports_scan_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); assets=root/"CustomAssets"; assets.mkdir()
            old=assets/"old.NIF"; old.write_bytes(header((4, 2, 2, 0))+b"synthetic payload")
            new=assets/"new.nif"; new.write_bytes(header((20, 0, 0, 4), "Gamebryo"))
            before={p.name:p.read_bytes() for p in assets.iterdir()}
            report=inspect_map(root)
            self.assertEqual(report["model_headers"]["files_checked"], 2)
            self.assertEqual(len(report["model_headers"]["findings"]), 1)
            self.assertIn("header-only risk flag", report["warnings"][0])
            self.assertEqual(before,{p.name:p.read_bytes() for p in assets.iterdir()})
            with patch("smr_launcher.model_checks.MAX_MODEL_FILES", 1):
                self.assertFalse(inspect_models(root)["complete"])

    def test_symlink_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/"UserMaps").mkdir()
            (root/"source").write_bytes(header((4, 2, 2, 0)))
            (root/"UserMaps/link.nif").symlink_to(root/"source")
            report=inspect_models(root)
            self.assertEqual(report["files_checked"],0)
            self.assertEqual(report["findings"][0]["status"],"unread")
            self.assertFalse(report["complete"])

    def test_dangling_nif_link_is_unread_and_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); maps=root/"UserMaps"; maps.mkdir()
            (maps/"missing.NIF").symlink_to(root/"not-present.nif")
            report=inspect_models(root)
            self.assertEqual(report["files_checked"],0)
            self.assertFalse(report["complete"])
            self.assertEqual(len(report["findings"]),1)
            self.assertEqual(report["findings"][0]["path"],"UserMaps/missing.NIF")
            self.assertEqual(report["findings"][0]["status"],"unread")

    def test_dangling_non_nif_link_is_unread_and_incomplete_without_following(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); maps=root/"UserMaps"; maps.mkdir()
            (maps/"unknown-target").symlink_to(root/"not-present")
            report=inspect_models(root)
            self.assertFalse(report["complete"])
            self.assertEqual(report["findings"][0]["path"],"UserMaps/unknown-target")
            self.assertEqual(report["findings"][0]["status"],"unread")

    def test_nonregular_nif_entry_is_never_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); maps=root/"UserMaps"; maps.mkdir()
            fifo=maps/"pipe.nif"
            os.mkfifo(fifo)
            with patch.object(Path, "open", side_effect=AssertionError("FIFO must not be opened")) as open_path:
                report=inspect_models(root)
            open_path.assert_not_called()
            self.assertFalse(report["complete"])
            self.assertEqual(report["files_checked"],0)
            self.assertEqual(report["findings"][0]["path"],"UserMaps/pipe.nif")
            self.assertEqual(report["findings"][0]["status"],"unread")

    def test_symlink_scope_and_root_are_not_traversed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root=Path(tmp); external=Path(outside)
            custom=root/"CustomAssets"; custom.mkdir()
            (external/"outside.nif").write_bytes(header((4,2,2,0)))
            (custom/"directory-link").symlink_to(external, target_is_directory=True)
            (root/"UserMaps").symlink_to(external, target_is_directory=True)
            report=inspect_models(root)
            self.assertFalse(report["complete"])
            self.assertEqual(report["files_checked"],0)
            self.assertEqual([f["path"] for f in report["findings"]],
                             ["CustomAssets/directory-link", "UserMaps"])

            root_link=root.parent/(root.name+"-link")
            try:
                root_link.symlink_to(root, target_is_directory=True)
                root_report=inspect_models(root_link)
                self.assertFalse(root_report["complete"])
                self.assertEqual(root_report["files_checked"],0)
                self.assertEqual(root_report["findings"][0]["status"],"unread")
            finally:
                root_link.unlink(missing_ok=True)

    def test_directory_scan_error_is_reported_and_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); blocked=root/"CustomAssets"/"blocked"
            blocked.mkdir(parents=True)
            (blocked/"hidden.nif").write_bytes(header((4,2,2,0)))
            real_scandir=os.scandir

            def scandir_with_error(path):
                if Path(path) == blocked:
                    raise PermissionError("synthetic permission denial")
                return real_scandir(path)

            with patch("smr_launcher.model_checks.os.scandir", side_effect=scandir_with_error):
                report=inspect_models(root)
            self.assertFalse(report["complete"])
            self.assertEqual(report["files_checked"],0)
            self.assertEqual(len(report["findings"]),1)
            self.assertEqual(report["findings"][0]["path"],"CustomAssets/blocked")
            self.assertEqual(report["findings"][0]["status"],"unread")

    def test_traversal_and_link_candidates_have_independent_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); maps=root/"UserMaps"; maps.mkdir()
            (root/"source").write_bytes(header((4,2,2,0)))
            for i in range(3): (maps/f"{i}.nif").symlink_to(root/"source")
            with patch("smr_launcher.model_checks.MAX_MODEL_FILES",1):
                report=inspect_models(root)
            self.assertFalse(report["complete"])
            self.assertEqual(len(report["findings"]),1)
            for p in maps.iterdir(): p.unlink()
            for i in range(3): (maps/f"{i}.txt").write_text("unrelated")
            with patch("smr_launcher.model_checks.MAX_TREE_ENTRIES",1):
                self.assertFalse(inspect_models(root)["complete"])

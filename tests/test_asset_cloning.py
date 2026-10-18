"""Synthetic asset copy characterization and native APFS isolation checks."""

from pathlib import Path
from unittest.mock import patch
import os
import stat
import unittest

import test_activation
import test_variants
from smr_launcher import activation, variants


class PreparationCopyBaselineTests(unittest.TestCase):
    setUp = test_variants.VariantPreparationTests.setUp

    def prepare(self, destination):
        return variants.prepare_original_variant(
            self.clean, self.imported, destination, "a" * 64, "b" * 64
        )

    def test_asset_timestamp_and_inode_isolation_survive_freeze(self):
        source = self.imported / "UserMaps/Example/RRT_Scenario_User_Example.xml"
        stamp = 1700000000123456789
        os.utime(source, ns=(stamp, stamp))
        before = source.stat()
        destination = self.root / "prepared"
        self.prepare(destination)
        copied = destination / source.relative_to(self.imported)
        self.assertEqual(copied.stat().st_mtime_ns, stamp)
        self.assertEqual(copied.stat().st_nlink, 1)
        self.assertNotEqual(copied.stat().st_ino, before.st_ino)
        copied.chmod(0o600)
        copied.write_bytes(b"private edit")
        self.assertEqual(source.read_bytes(), b"<Scenario/>")
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), stat.S_IMODE(before.st_mode))
        source.chmod(0o400)
        self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o600)
        source.chmod(0o600)
        source.write_bytes(b"source edit")
        self.assertEqual(copied.read_bytes(), b"private edit")

    def test_copy_failure_never_publishes_partial_prepared_tree(self):
        destination = self.root / "prepared"
        with patch.object(variants.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.prepare(destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".smr-variant-*")), [])


class ProfileCopyBaselineTests(unittest.TestCase):
    setUp = test_activation.ActivationTests.setUp
    make_manager = test_activation.ActivationTests.make_manager

    def source(self):
        source = self.root / "prepared"
        for name in ("CustomAssets", "UserMaps", "Saves"):
            (source / name).mkdir(parents=True)
        (source / "CustomAssets/asset").write_bytes(b"asset")
        (source / "Saves/save").write_bytes(b"synthetic save")
        return source

    def test_registration_preserves_times_and_writable_independence(self):
        source = self.source()
        asset = source / "CustomAssets/asset"
        stamp = 1700000000123456789
        os.utime(asset, ns=(stamp, stamp))
        asset.chmod(0o400)
        profile = self.manager.register_variant("a" * 64, source)
        copied = self.store / "profiles" / profile / "CustomAssets/asset"
        self.assertEqual(copied.stat().st_mtime_ns, stamp)
        self.assertEqual(copied.stat().st_nlink, 1)
        self.assertNotEqual(copied.stat().st_ino, asset.stat().st_ino)
        self.assertEqual(stat.S_IMODE(asset.stat().st_mode), 0o400)
        copied.write_bytes(b"profile edit")
        self.assertEqual(asset.read_bytes(), b"asset")
        self.assertTrue((self.store / "profiles" / profile / "Saves/save").exists())

    def test_registration_copy_failure_cleans_unpublished_generation(self):
        source = self.source()
        with patch.object(self.manager, "_copytree", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.manager.register_variant("a" * 64, source)
        self.assertFalse((self.store / "profiles" / ("map-" + "a" * 64)).exists())
        self.assertEqual(list(self.manager.staging.iterdir()), [])


class NativeAssetCopyTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source"
        self.source.write_bytes(os.urandom(1024 * 1024))
        self.target = self.root / "target"

    def test_native_clone_is_exercised_and_keeps_metadata_and_mutations_private(self):
        from smr_launcher import asset_copy

        stamp = 1700000000123456789
        os.utime(self.source, ns=(stamp, stamp))
        self.source.chmod(0o640)
        with (
            patch.object(asset_copy, "_ordinary_copy", side_effect=AssertionError("fallback")),
            patch.object(asset_copy, "_clone", wraps=asset_copy._clone) as clone,
        ):
            asset_copy.copy_asset(self.source, self.target)
        clone.assert_called_once()
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.target.stat().st_mtime_ns, stamp)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertNotEqual(self.target.stat().st_ino, self.source.stat().st_ino)
        self.assertEqual(self.target.stat().st_nlink, 1)
        self.assertEqual(self.source.stat().st_nlink, 1)
        self.target.chmod(0o600)
        self.target.write_bytes(b"destination changed")
        self.assertEqual(self.source.stat().st_size, 1024 * 1024)
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o640)
        self.source.chmod(0o400)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.source.chmod(0o600)
        self.source.write_bytes(b"source changed")
        self.assertEqual(self.target.read_bytes(), b"destination changed")

    def test_unsupported_and_cross_volume_clone_fall_back_but_real_errors_propagate(self):
        import errno
        from smr_launcher import asset_copy

        for number in (errno.ENOTSUP, errno.EXDEV):
            with (
                self.subTest(number=number),
                patch.object(asset_copy, "_clone", side_effect=OSError(number, "unsupported")),
                patch.object(
                    asset_copy, "_ordinary_copy", wraps=asset_copy._ordinary_copy
                ) as copied,
            ):
                asset_copy.copy_asset(self.source, self.target)
                copied.assert_called_once()
                self.assertEqual(self.source.read_bytes(), self.target.read_bytes())
                self.target.unlink()
        for number in (errno.EACCES, errno.ENOSPC, errno.EIO):
            with (
                self.subTest(number=number),
                patch.object(asset_copy, "_clone", side_effect=OSError(number, "failure")),
                patch.object(asset_copy, "_ordinary_copy", side_effect=AssertionError("fallback")),
            ):
                with self.assertRaises(OSError) as raised:
                    asset_copy.copy_asset(self.source, self.target)
                self.assertEqual(raised.exception.errno, number)
                self.assertFalse(self.target.exists())

    def test_source_drift_and_partial_failure_remove_only_owned_destination(self):
        from smr_launcher import asset_copy

        original = asset_copy._clone

        def drift(*args):
            original(*args)
            self.source.write_bytes(b"changed")

        with patch.object(asset_copy, "_clone", drift):
            with self.assertRaisesRegex(OSError, "changed"):
                asset_copy.copy_asset(self.source, self.target)
        self.assertFalse(self.target.exists())
        self.target.write_bytes(b"foreign")
        with self.assertRaises(FileExistsError):
            asset_copy.copy_asset(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b"foreign")

    def test_linked_sources_are_refused(self):
        from smr_launcher import asset_copy

        linked = self.root / "linked"
        linked.symlink_to(self.source)
        with self.assertRaises((OSError, activation.ActivationError)):
            asset_copy.copy_asset(linked, self.target)
        os.link(self.source, self.root / "hardlink")
        with self.assertRaises(OSError):
            asset_copy.copy_asset(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_native_and_ordinary_paths_preserve_extended_attributes(self):
        import errno
        import subprocess
        from smr_launcher import asset_copy

        subprocess.run(
            [
                "/usr/bin/xattr",
                "-w",
                "com.craigfranklin.synthetic",
                "asset metadata",
                str(self.source),
            ],
            check=True,
        )
        subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(self.source)], check=True)
        acl = subprocess.run(
            ["/bin/ls", "-le", str(self.source)], check=True, capture_output=True, text=True
        ).stdout.splitlines()[1:]
        self.assertTrue(acl)
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                if fallback:
                    with patch.object(
                        asset_copy, "_clone", side_effect=OSError(errno.EXDEV, "cross volume")
                    ):
                        asset_copy.copy_asset(self.source, self.target)
                else:
                    asset_copy.copy_asset(self.source, self.target)
                value = subprocess.run(
                    ["/usr/bin/xattr", "-p", "com.craigfranklin.synthetic", str(self.target)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(value.stdout.strip(), "asset metadata")
                copied_acl = subprocess.run(
                    ["/bin/ls", "-le", str(self.target)], check=True, capture_output=True, text=True
                ).stdout.splitlines()[1:]
                self.assertEqual(copied_acl, acl)
                self.target.unlink()

    def test_partial_native_error_cleans_private_scratch(self):
        import errno
        from smr_launcher import asset_copy

        def partial(source_fd, directory_fd, name):
            fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory_fd)
            os.write(fd, b"partial")
            os.close(fd)
            raise OSError(errno.ENOSPC, "disk full")

        with patch.object(asset_copy, "_clone", partial):
            with self.assertRaises(OSError):
                asset_copy.copy_asset(self.source, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob(".asset-copy-*")), [])


class AssetIntegrationTests(ProfileCopyBaselineTests):
    def test_only_asset_roots_clone_and_observed_lane_keeps_adapter(self):
        import fcntl
        from smr_launcher import asset_copy

        source = self.source()
        (source / "Settings.ini").write_bytes(b"settings")
        cloned = []
        original = asset_copy._clone

        def clone(source_fd, *args):
            cloned.append(os.fsdecode(fcntl.fcntl(source_fd, 50, b"\0" * 1024).split(b"\0", 1)[0]))
            return original(source_fd, *args)

        with patch.object(asset_copy, "_clone", clone):
            self.manager.register_variant("b" * 64, source)
        self.assertEqual(len(cloned), 1)
        self.assertTrue(cloned[0].endswith("/CustomAssets/asset"))

        class Adapter:
            def copytree(self, incoming, outgoing):
                return "observed-copy"

        self.manager.observed_io = Adapter()
        with patch.object(
            asset_copy, "_clone", side_effect=AssertionError("clone in observed lane")
        ):
            self.assertEqual(
                self.manager._copytree(source, self.root / "observed", clone_assets=True),
                "observed-copy",
            )

    def test_process_interruption_before_profile_publication_keeps_source_and_recovery(self):
        source = self.source()
        variant = "c" * 64
        pid = os.fork()
        if pid == 0:
            self.manager._fsync_tree = lambda path: os._exit(93)
            self.manager.register_variant(variant, source)
            os._exit(94)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 93)
        self.assertFalse((self.manager.profiles / ("map-" + variant)).exists())
        self.assertEqual((source / "CustomAssets/asset").read_bytes(), b"asset")
        self.assertEqual(self.manager.recover(), activation.ORIGINAL)
        profile = self.manager.register_variant(variant, source)
        self.assertTrue((self.manager.profiles / profile / "CustomAssets/asset").exists())

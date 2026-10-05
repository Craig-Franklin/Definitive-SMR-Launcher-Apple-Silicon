"""Synthetic metadata-aware resource fingerprint tests."""
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from smr_launcher import resource_identity as identity


class ResourceIdentityTests(unittest.TestCase):
    def make_assets(self, root: Path) -> Path:
        assets = root / "Assets"
        assets.mkdir(parents=True)
        (assets / "xml").mkdir()
        (assets / "xml" / "RRT_Goods.xml").write_bytes(b"<Goods />")
        (assets / "content.FPK").write_bytes(b"FPK\x00serialized-member-time\x01")
        return assets

    def fingerprint(self, assets: Path, **kwargs):
        return identity.fingerprint_resources({"installed_assets": assets}, **kwargs)

    def test_metadata_only_mtime_change_changes_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = self.make_assets(Path(temporary).resolve())
            goods = assets / "xml/RRT_Goods.xml"
            before = self.fingerprint(assets)
            st = goods.stat()
            os.utime(goods, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            after = self.fingerprint(assets)

            self.assertNotEqual(before["identity"], after["identity"])
            self.assertEqual(before["roots"][0]["entries"][-1]["path"], "xml/RRT_Goods.xml")

    def test_optional_cache_preserves_fingerprint_and_skips_unchanged_file_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = self.make_assets(Path(temporary).resolve())
            cache = identity.ContentHashCache()
            uncached = self.fingerprint(assets)
            first_cached = self.fingerprint(assets, hash_cache=cache)
            self.assertEqual(uncached["identity"], first_cached["identity"])
            self.assertEqual(cache.misses, 2)

            real_read = identity.os.read
            with patch.object(identity.os, "read", wraps=real_read) as read:
                second_cached = self.fingerprint(assets, hash_cache=cache)

            self.assertEqual(uncached["identity"], second_cached["identity"])
            self.assertEqual(read.call_count, 0)
            self.assertEqual(cache.hits, 2)
            self.assertEqual(second_cached["totals"]["bytes"], uncached["totals"]["bytes"])

    def test_cache_misses_for_same_length_mutation_with_restored_mtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = self.make_assets(Path(temporary).resolve())
            target = assets / "content.FPK"
            cache = identity.ContentHashCache()
            before = self.fingerprint(assets, hash_cache=cache)
            original_stat = target.stat()
            time.sleep(0.01)
            target.write_bytes(b"FPK\x00serialized-member-time\x03")
            os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            changed_stat = target.stat()
            self.assertEqual(changed_stat.st_size, original_stat.st_size)
            self.assertEqual(changed_stat.st_mtime_ns, original_stat.st_mtime_ns)
            self.assertNotEqual(changed_stat.st_ctime_ns, original_stat.st_ctime_ns)

            real_read = identity.os.read
            with patch.object(identity.os, "read", wraps=real_read) as read:
                after = self.fingerprint(assets, hash_cache=cache)

            self.assertNotEqual(before["identity"], after["identity"])
            self.assertGreater(read.call_count, 0)
            self.assertEqual(cache.misses, 3)  # Two initial files and the changed stat key.
            self.assertEqual(cache.hits, 1)  # The unchanged XML file.

    def test_cache_retraverses_rename_addition_and_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = self.make_assets(Path(temporary).resolve())
            cache = identity.ContentHashCache()
            initial = self.fingerprint(assets, hash_cache=cache)
            (assets / "content.FPK").rename(assets / "renamed.FPK")
            renamed = self.fingerprint(assets, hash_cache=cache)
            self.assertNotEqual(initial["identity"], renamed["identity"])

            (assets / "new.FPK").write_bytes(b"new content")
            added = self.fingerprint(assets, hash_cache=cache)
            self.assertNotEqual(renamed["identity"], added["identity"])

            (assets / "xml/RRT_Goods.xml").unlink()
            deleted = self.fingerprint(assets, hash_cache=cache)
            self.assertNotEqual(added["identity"], deleted["identity"])

    def test_cache_is_lru_bounded_and_hits_still_count_against_byte_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = self.make_assets(Path(temporary).resolve())
            cache = identity.ContentHashCache(max_entries=1)
            self.fingerprint(assets, hash_cache=cache)
            self.assertEqual(len(cache), 1)
            self.assertLessEqual(len(cache), cache.max_entries)
            with self.assertRaisesRegex(identity.ResourceFingerprintError, "byte limit"):
                self.fingerprint(assets, hash_cache=cache, max_total_bytes=1)

    def test_location_copy2_preserving_tree_metadata_keeps_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source = self.make_assets(base / "source")
            before = self.fingerprint(source)
            copy = base / "elsewhere" / "Assets"
            copy.parent.mkdir()
            shutil.copytree(source, copy, copy_function=shutil.copy2)
            after = self.fingerprint(copy)

            self.assertEqual(before["identity"], after["identity"])
            self.assertNotEqual(before["roots"][0]["location"], after["roots"][0]["location"])

    def test_fpk_bytes_are_hashed_even_when_file_mtime_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = self.make_assets(Path(temporary).resolve())
            archive = assets / "content.FPK"
            before = self.fingerprint(assets)
            st = archive.stat()
            archive.write_bytes(b"FPK\x00serialized-member-time\x02")
            os.utime(archive, ns=(st.st_atime_ns, st.st_mtime_ns))
            after = self.fingerprint(assets)

            self.assertEqual(st.st_mtime_ns, archive.stat().st_mtime_ns)
            self.assertNotEqual(before["identity"], after["identity"])

    def test_symlink_entry_fails_closed_without_following_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            assets = self.make_assets(base)
            secret = base / "outside-secret"
            secret.write_bytes(b"not a resource")
            (assets / "linked-resource").symlink_to(secret)

            with self.assertRaisesRegex(identity.ResourceFingerprintError, "Symlink"):
                self.fingerprint(assets)

    def test_symlink_root_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            real = self.make_assets(base / "real")
            link = base / "Assets"
            link.symlink_to(real, target_is_directory=True)

            with self.assertRaisesRegex(identity.ResourceFingerprintError, "Symlink resource root"):
                self.fingerprint(link)

    def test_symlink_in_root_ancestor_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            real_parent = base / "real-parent"
            assets = self.make_assets(real_parent)
            linked_parent = base / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)

            with self.assertRaisesRegex(identity.ResourceFingerprintError, "Symlink root ancestor"):
                self.fingerprint(linked_parent / "Assets")

    def test_unreadable_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = self.make_assets(Path(temporary).resolve())
            target = assets / "content.FPK"
            real_open = identity.os.open

            def deny(path, flags, *args, **kwargs):
                if os.fspath(path) == target.name and kwargs.get("dir_fd") is not None:
                    raise PermissionError("synthetic unreadable resource")
                return real_open(path, flags, *args, **kwargs)

            with patch.object(identity.os, "open", side_effect=deny):
                with self.assertRaisesRegex(identity.ResourceFingerprintError, "Cannot open resource file"):
                    self.fingerprint(assets)

    def test_file_replacement_during_read_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            assets = base / "Assets"
            assets.mkdir()
            target = assets / "large.FPK"
            target.write_bytes(b"A" * (2 * identity.CHUNK_SIZE))
            replacement = base / "replacement"
            replacement.write_bytes(b"B" * (2 * identity.CHUNK_SIZE))
            real_read = identity.os.read
            replaced = False

            def replace_after_chunk(fd, size):
                nonlocal replaced
                data = real_read(fd, size)
                if data and not replaced:
                    replaced = True
                    os.replace(replacement, target)
                return data

            with patch.object(identity.os, "read", side_effect=replace_after_chunk):
                with self.assertRaisesRegex(identity.ResourceFingerprintError, "changed while reading"):
                    self.fingerprint(assets)
            self.assertTrue(replaced)

    def test_regular_file_to_fifo_swap_fails_without_blocking_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = self.make_assets(Path(temporary).resolve())
            target = assets / "content.FPK"
            real_open = identity.os.open
            swapped = False

            def fifo_swap(path, flags, *args, **kwargs):
                nonlocal swapped
                if os.fspath(path) == target.name and kwargs.get("dir_fd") is not None and not swapped:
                    swapped = True
                    os.unlink(target)
                    os.mkfifo(target)
                    self.assertTrue(flags & getattr(os, "O_NONBLOCK", 0),
                                    "resource opens must be nonblocking against a raced FIFO")
                return real_open(path, flags, *args, **kwargs)

            with patch.object(identity.os, "open", side_effect=fifo_swap):
                with self.assertRaisesRegex(identity.ResourceFingerprintError, "changed before reading"):
                    self.fingerprint(assets)
            self.assertTrue(swapped)

    def test_root_path_replacement_after_open_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            assets = self.make_assets(base / "original")
            moved = assets.with_name("MovedAssets")
            real_walk = identity._Walker._walk_dir
            replaced = False

            def replace_after_walk(walker, directory_fd, label, relative, depth, opened_stat=None):
                nonlocal replaced
                result = real_walk(walker, directory_fd, label, relative, depth, opened_stat)
                if depth == 0 and not replaced:
                    replaced = True
                    assets.rename(moved)
                    assets.mkdir()
                return result

            with patch.object(identity._Walker, "_walk_dir", new=replace_after_walk):
                with self.assertRaisesRegex(identity.ResourceFingerprintError, "path was replaced"):
                    self.fingerprint(assets)
            self.assertTrue(replaced)

    def test_missing_supplied_root_and_omitted_required_role_are_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            with self.assertRaisesRegex(identity.ResourceFingerprintError, "Missing/unreadable root"):
                self.fingerprint(base / "missing" / "Assets")
            assets = self.make_assets(base)
            with self.assertRaisesRegex(identity.ResourceFingerprintError, "Required resource roots"):
                identity.fingerprint_resources({"installed_assets": assets},
                                               required_roles=("user_maps",))

    def test_full_profile_root_is_rejected_and_siblings_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary).resolve() / "profile"
            assets = profile / "Assets"
            custom = profile / "CustomAssets"
            maps = profile / "UserMaps"
            for path in (assets, custom, maps):
                path.mkdir(parents=True, exist_ok=True)
            (profile / "Settings.ini").write_bytes(b"settings")
            (profile / "Saves").mkdir()
            (profile / "Logs").mkdir()
            (custom / "override.xml").write_bytes(b"override")
            (maps / "map.xml").write_bytes(b"map")

            with self.assertRaisesRegex(identity.ResourceFingerprintError, "must name a assets directory"):
                identity.fingerprint_resources({"installed_assets": profile})
            result = identity.fingerprint_resources({
                "installed_assets": assets,
                "custom_assets": custom,
                "user_maps": maps,
            }, required_roles=("installed_assets", "custom_assets", "user_maps"))
            encoded = repr(result)
            self.assertNotIn("Settings.ini", encoded)
            self.assertNotIn("Saves", encoded)
            self.assertNotIn("Logs", encoded)
            self.assertEqual({root["role"] for root in result["roots"]}, {
                "installed_assets", "custom_assets", "user_maps"})

    def test_entry_and_byte_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = self.make_assets(Path(temporary).resolve())
            with self.assertRaisesRegex(identity.ResourceFingerprintError, "entry limit"):
                self.fingerprint(assets, max_entries=1)
            with self.assertRaisesRegex(identity.ResourceFingerprintError, "byte limit"):
                self.fingerprint(assets, max_total_bytes=1)


if __name__ == "__main__":
    unittest.main()

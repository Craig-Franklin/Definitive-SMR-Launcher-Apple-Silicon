"""Changed-size XML streams cannot bypass the admitted scan byte budget."""

from io import BytesIO
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from smr_launcher import import_checks, resource_identity
import test_import_check_freshness as fixtures


class ImportCheckReadBudgetTests(unittest.TestCase):
    def controlled_stream(self, target, data, requests):
        original_open = Path.open

        class Stream(BytesIO):
            def read(self, size=-1):
                requests.append(size)
                return super().read(size)

        def opening(path, *args, **kwargs):
            if path == target:
                return Stream(data)
            return original_open(path, *args, **kwargs)

        return patch.object(Path, "open", opening)

    def test_stable_boundary_and_changed_stream_sizes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            maps = root / "UserMaps"
            maps.mkdir()
            target = maps / "a.xml"
            target.write_bytes(b"<a/>")
            original = target.read_bytes(), target.stat()
            for data in (b"<a/>", b"<a>" + b" " * 58 + b"</a>", b" "):
                with self.subTest(stream_bytes=len(data)):
                    requests = []
                    with (
                        self.controlled_stream(target, data, requests),
                        patch.multiple(import_checks, MAX_XML_BYTES=4, MAX_TOTAL_XML=4),
                    ):
                        if data == b"<a/>":
                            report = import_checks.inspect_map(root)
                            self.assertTrue(report["xml_scan"]["complete"])
                            self.assertEqual(report["warnings"], [])
                        else:
                            with self.assertRaisesRegex(OSError, "size changed"):
                                import_checks.inspect_map(root)
                    self.assertEqual(requests, [5])
                    self.assertEqual(target.read_bytes(), original[0])
                    self.assertEqual(target.stat().st_size, original[1].st_size)
                    self.assertEqual(target.stat().st_mtime_ns, original[1].st_mtime_ns)

    def test_changed_stream_preserves_prior_cached_report(self):
        fixture = fixtures.ImportCheckFreshnessTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        before = fixture.report_path.read_bytes()
        root = fixture.app._prepared_root(fixture.record)
        target = root / "UserMaps/Example/RRT_Scenario_User_Example.xml"
        size = target.stat().st_size
        requests = []
        target_inode = target.stat().st_ino
        original_read = os.read

        def changed_stream(fd, count):
            data = original_read(fd, count)
            if os.fstat(fd).st_ino == target_inode:
                requests.append(count)
                if data:
                    return data + b"extra unadmitted bytes"
            return data

        with patch.object(resource_identity.os, "read", side_effect=changed_stream):
            with self.assertRaisesRegex(RuntimeError, "size changed"):
                fixture.app.import_checks(fixture.record, refresh=True)
        self.assertTrue(requests)
        self.assertTrue(all(0 < n <= resource_identity.CHUNK_SIZE for n in requests))
        self.assertEqual(target.stat().st_size, size)
        self.assertEqual(fixture.report_path.read_bytes(), before)
        self.assertIsNone(fixture.app.verification_for(fixture.record))


if __name__ == "__main__":
    unittest.main()

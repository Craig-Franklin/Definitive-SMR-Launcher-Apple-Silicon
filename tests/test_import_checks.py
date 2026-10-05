"""Static findings never claim or manufacture gameplay verification."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_application
from smr_launcher.import_checks import inspect_map

class ImportChecksTests(unittest.TestCase):
    def test_xml_findings_and_stock_lookup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); maps=root/'UserMaps'; maps.mkdir()
            (maps/'scenario.xml').write_text('<Scenario><Bridge>RRT_Bridges.xml</Bridge><Stock>stock.xml</Stock><Missing>absent.xml</Missing></Scenario>')
            (maps/'RRT_ Bridges.xml').write_text('<Bridges/>')
            (maps/'bad.xml').write_text('<broken>')
            (maps/'entity.xml').write_text('<!DOCTYPE a [<!ENTITY x "x">]><a>&x;</a>')
            before={p.name:p.read_bytes() for p in maps.iterdir()}
            report=inspect_map(root, {'stock.xml'})
            text='\n'.join(report['warnings'])
            self.assertIn('possible filename mismatch', text)
            self.assertIn('absent.xml', text)
            self.assertNotIn('stock.xml', text)
            self.assertIn('could not be parsed', text)
            self.assertIn('declarations require manual', text)
            self.assertIn('do not run the game', report['limitations'])
            self.assertEqual(before, {p.name:p.read_bytes() for p in maps.iterdir()})
            with patch('smr_launcher.import_checks.MAX_XML_FILES', 1):
                self.assertIn('limit reached', '\n'.join(inspect_map(root)['warnings']))

    def test_import_creates_report_without_gameplay_badge(self):
        fixture=test_application.ApplicationTests(); fixture.setUp()
        try:
            app=fixture.application; app.setup(); record=app.import_archive(fixture.archive)
            report=app.import_checks(record)
            self.assertEqual(report['xml_files_checked'],1)
            self.assertEqual(report['warnings'],[])
            self.assertIsNone(app.verification_for(record))
            self.assertEqual(app.import_checks(record, refresh=True)['variant_id'],record.variant_id)
        finally: fixture.doCleanups()

if __name__ == '__main__': unittest.main()

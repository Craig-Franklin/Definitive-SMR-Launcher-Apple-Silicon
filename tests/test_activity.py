from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from smr_launcher.activity import ActivityLog

class ActivityTests(unittest.TestCase):
    def test_persistence_tail_bound_corruption_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve(); log=ActivityLog(root)
            self.assertEqual(log.records(),())
            with patch('smr_launcher.activity.MAX_BYTES',4096):
                for i in range(50): log.append(str(i),'Completed','detail'*30)
                self.assertLessEqual(log.path.stat().st_size,4096)
                self.assertEqual(ActivityLog(root).records()[-1]['action'],'49')
            with log.path.open('ab') as stream: stream.write(b'broken line\n')
            self.assertEqual(log.records()[-1]['action'],'49')
            log.path.unlink(); target=root/'keep'; target.write_text('unchanged')
            log.path.symlink_to(target)
            with self.assertRaises(Exception): log.append('test','test')
            self.assertEqual(target.read_text(),'unchanged')

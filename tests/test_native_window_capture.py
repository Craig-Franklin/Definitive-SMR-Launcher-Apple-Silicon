"""Ensure the isolated research capture bundle runs from an ordinary checkout."""
from pathlib import Path
import subprocess
import sys
import unittest

class NativeWindowCaptureTests(unittest.TestCase):
    def test_isolated_fake_controls(self):
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([sys.executable,'-B',str(root/'scripts/research/native_window_capture/check.py')],cwd=root,capture_output=True,text=True,timeout=195)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':unittest.main()

"""Run shipped fake-only checks from an isolated private copy, with no native calls."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

def main():
    source=Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='native-capture-check-',dir=Path.cwd().resolve()) as name:
        private=Path(name);private.chmod(0o700)
        for path in [*source.glob('*.py'),source/'WindowProbe.swift']:shutil.copyfile(path,private/path.name)
        for path in (source/'tests').glob('*.py'):shutil.copyfile(path,private/path.name)
        (private/'temp0700').mkdir(mode=0o700);(private/'temp').mkdir(mode=0o700)
        environment=dict(os.environ,PYTHONPATH=str(private),PYTHONDONTWRITEBYTECODE='1')
        for script in ('offline_suite.py','consumer_regression.py'):
            result=subprocess.run([sys.executable,'-B',str(private/script)],cwd=private,env=environment,timeout=95)
            if result.returncode:return result.returncode
    return 0
if __name__=='__main__':raise SystemExit(main())

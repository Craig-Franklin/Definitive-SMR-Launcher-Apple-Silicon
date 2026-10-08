"""Run the isolated support bundle's fake-only checks in a private copy."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    source = Path(__file__).resolve().parent
    names = ('wrapper.py', 'adapter.py', 'observer.py', 'individual_stop.py',
             'frozen_mac_processes.py', 'consumer.py', 'host.py', 'pins.json',
             'proposal.json')
    # Resolve macOS /var and checkout links before the nofollow admission checks.
    with tempfile.TemporaryDirectory(prefix='individual-stop-check-',
                                     dir=Path.cwd().resolve()) as temporary:
        private = Path(temporary)
        private.chmod(0o700)
        for name in names:
            shutil.copyfile(source/name, private/name)
        shutil.copytree(source/'tests', private/'tests')
        environment = dict(os.environ, PYTHONPATH=str(private),
                           PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run(
            [sys.executable, '-B', '-m', 'unittest', 'discover',
             '-s', str(private/'tests'), '-p', 'test_*.py', '-v'],
            cwd=private, env=environment, timeout=60,
        ).returncode


if __name__ == '__main__':
    raise SystemExit(main())

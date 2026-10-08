"""Stop-only individual helper entry point; root alone launches the game.

Only a private root admission and the fixed local reviewed modules are consumed.
The root verifies every imported byte before this process starts. No scanning,
game launch, GUI, save, restoration, batch, recovery or retry is implemented.
"""
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from wrapper import Wrapper, RootAdmission, bounded, publish, parent_stamp
from frozen_mac_processes import MacProcesses


def main(packet):
    import json
    packet = Path(packet)
    raw = json.loads(bounded(packet/'admission.json'))
    admission = RootAdmission(**raw['admission'])
    if (Path(sys.executable).absolute() != Path(admission.interpreter) or
            admission.clock_domain != 'root-venv-time.monotonic-v1'):
        raise ValueError('actual interpreter/domain differs from root')
    # The existing root interpreter supplies this same clock before watchdog
    # arming. Root independently compares both clocks, not these strings alone.
    wrapper = Wrapper(admission, packet, raw['approval'], raw['source_acceptance'],
                      MacProcesses, time.monotonic, time.sleep)
    own = MacProcesses(sys.executable).inspect(os.getpid())
    if own is None:
        raise ValueError('helper own identity unavailable')
    bootstrap = {'version': 1, 'job': admission.job, 'nonce': admission.nonce,
                 'code': admission.code, 'anchor': admission.anchor,
                 'interpreter': sys.executable, 'prefix': sys.prefix,
                 'clock_domain': admission.clock_domain,
                 'monotonic': time.monotonic(),
                 'clock': vars(time.get_clock_info('monotonic')),
                 'process': asdict(own)}
    publish(packet, 'bootstrap.json', bootstrap, parent_stamp(packet))
    state = wrapper.run()
    return 0 if state.phase == 'observed_exited' and wrapper.error is None else 2


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))

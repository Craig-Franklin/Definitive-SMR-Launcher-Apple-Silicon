"""Proposal127-conditional operational adapter, NOT atomic replacement refusal.

No MacProcesses import or construction here. Root supplies a separately accepted
factory for the pinned frozen implementation; tests supply only pure fakes.
The copied component's stronger contract is NOT discharged by this adapter.
"""
from individual_stop import ProcessIdentity

class Adapter:
    def __init__(self, process_factory, expected_executable):
        self.expected_executable = expected_executable
        self.backend = process_factory(expected_executable)

    def inspect(self, pid):
        p = self.backend.inspect(pid)
        if p is None:
            return None
        if (type(p.pid) is not int or p.pid != pid or
                type(p.birth_us) is not int or p.birth_us <= 0 or
                p.executable != self.expected_executable):
            raise ValueError('unexpected process identity')
        return ProcessIdentity(p.pid, str(p.birth_us), p.executable)

    def request_stop(self, expected):
        if (type(expected) is not ProcessIdentity or
                expected.executable != self.expected_executable or
                not expected.birth.isdecimal() or
                str(int(expected.birth)) != expected.birth):
            raise ValueError('expected identity mismatch')
        current = self.backend.inspect(expected.pid)
        if (current is None):
            return
        if (type(current.pid) is not int or type(current.birth_us) is not int or
                (current.pid, str(current.birth_us), current.executable) !=
                (expected.pid, expected.birth, expected.executable)):
            raise ValueError('replacement; no delegation')
        # Existing frozen stop rechecks before each TERM/KILL; check-to-signal
        # race remains. Explicit approval and separately accepted binding needed.
        self.backend.stop(current)

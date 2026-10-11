"""Bounded resource protection for the exact observed Railroads process."""
import ctypes
import ctypes.util
import os
import signal
import time

GIB = 1024 ** 3
FOOTPRINT_LIMIT = 8 * GIB
HARD_FOOTPRINT_LIMIT = 16 * GIB
SUSTAINED_SECONDS = 5


class _Usage(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in
        ("user", "system", "idle", "interrupts", "pageins", "wired", "resident",
         "footprint", "start", "exit")]


def physical_footprint(process):
    """Include compressed/swapped allocations; RSS hides this failure mode."""
    from .crash_monitor import running_game
    if running_game(process["executable"]) != process:
        return None
    lib = ctypes.CDLL(ctypes.util.find_library("proc"), use_errno=True)
    lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.proc_pid_rusage.restype = ctypes.c_int
    usage = _Usage()
    if lib.proc_pid_rusage(process["pid"], 0, ctypes.byref(usage)) != 0:
        raise OSError("Game resource observation unavailable")
    if running_game(process["executable"]) != process:
        return None
    return int(usage.footprint)


def stop_game(process, *, probe=None, send_signal=os.kill, wait=time.sleep, clock=time.monotonic):
    """Recheck path, PID and birth immediately before every signal; never relaunch."""
    from .crash_monitor import running_game
    probe = probe or running_game
    for sig, seconds in ((signal.SIGTERM, 2), (signal.SIGKILL, 3)):
        current = probe(process["executable"])
        if current is None:
            return
        if current != process:
            raise OSError("Game identity changed; stop refused")
        try:
            send_signal(process["pid"], sig)
        except ProcessLookupError:
            return
        deadline = clock() + seconds
        while clock() < deadline:
            current = probe(process["executable"])
            if current is None:
                return
            if current != process:
                raise OSError("Game identity changed; further signals refused")
            wait(0.1)
    raise OSError("Game stop could not be confirmed")


class ResourceGuard:
    def __init__(self, *, probe=physical_footprint, stopper=stop_game, clock=time.monotonic):
        self.probe, self.stopper, self.clock = probe, stopper, clock
        self.identity = None
        self.over_since = None
        self.handled = False
        self.retention_failed = False

    def observe(self, process, retain):
        if process != self.identity:
            self.identity, self.over_since, self.handled = process, None, False
            self.retention_failed = False
        if process is None or self.handled:
            return None
        try:
            footprint = self.probe(process)
        except Exception:
            self.over_since = None
            raise
        if type(footprint) is not int or footprint < FOOTPRINT_LIMIT:
            self.over_since = None
            return None
        now = self.clock()
        if self.over_since is None:
            self.over_since = now
        if footprint < HARD_FOOTPRINT_LIMIT and now - self.over_since < SUSTAINED_SECONDS:
            return None
        # Attempt durable diagnostics first. Full disks (including swap pressure)
        # must not prevent stopping an already runaway game.
        try:
            retain(process, footprint)
        except Exception:
            self.retention_failed = True
        self.handled = True
        self.stopper(process)
        return footprint

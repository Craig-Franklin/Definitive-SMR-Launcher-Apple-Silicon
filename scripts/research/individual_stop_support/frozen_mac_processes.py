import ctypes
import ctypes.util
import os
import signal
import sys
import time
from pathlib import Path
from dataclasses import dataclass

class SafetyError(RuntimeError):
    pass


class BSDInfo(ctypes.Structure):
    _fields_ = [("prefix", ctypes.c_uint32 * 12), ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32), ("suffix", ctypes.c_uint32 * 6),
        ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]


class Usage(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in
        ("user", "system", "idle", "interrupts", "pageins", "wired", "resident",
         "footprint", "start", "exit")]


@dataclass(frozen=True)
class Process:
    pid: int
    birth_us: int
    executable: str
    footprint: int

    def same(self, other):
        return other is not None and (self.pid, self.birth_us, self.executable) == (
            other.pid, other.birth_us, other.executable)


class MacProcesses:
    """libproc identity and physical footprint; never attach a debugger."""
    def __init__(self, executable):
        if sys.platform != "darwin":
            raise SafetyError("Runtime tests require macOS")
        self.executable = str(Path(executable).resolve())
        self.lib = ctypes.CDLL(ctypes.util.find_library("proc"), use_errno=True)
        for name, args in {
            "proc_listallpids": [ctypes.c_void_p, ctypes.c_int],
            "proc_pidpath": [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32],
            "proc_name": [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32],
            "proc_pidinfo": [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int],
            "proc_pid_rusage": [ctypes.c_int, ctypes.c_int, ctypes.c_void_p],
        }.items():
            fn = getattr(self.lib, name); fn.argtypes = args; fn.restype = ctypes.c_int

    def inspect(self, pid):
        path = ctypes.create_string_buffer(4096)
        if self.lib.proc_pidpath(pid, path, len(path)) <= 0:
            return self._failed_inspection(pid, "Cannot identify a live process")
        executable = os.fsdecode(path.value)
        info = BSDInfo(); usage = Usage()
        if self.lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
            return self._failed_inspection(pid, "Cannot read process birth identity")
        if self.lib.proc_pid_rusage(pid, 0, ctypes.byref(usage)) != 0:
            return self._failed_inspection(pid, "Cannot read process physical footprint")
        return Process(pid, info.start_sec * 1000000 + info.start_usec, executable, usage.footprint)

    @staticmethod
    def _failed_inspection(pid, reason):
        # Normal exit can race the separate libproc reads. Only independent
        # evidence that the PID is gone permits treating that race as exit.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
        raise SafetyError(reason)

    def games(self):
        count = self.lib.proc_listallpids(None, 0)
        if count <= 0: raise SafetyError("Process enumeration failed")
        pids = (ctypes.c_int * max(1024, count * 2))()
        count = self.lib.proc_listallpids(pids, ctypes.sizeof(pids))
        if count <= 0 or count >= len(pids): raise SafetyError("Incomplete process enumeration")
        found = []
        for pid in pids[:count]:
            if pid <= 0: continue
            path = ctypes.create_string_buffer(4096)
            name = ctypes.create_string_buffer(1024)
            length = self.lib.proc_pidpath(pid, path, len(path))
            self.lib.proc_name(pid, name, len(name))
            if length > 0 and os.fsdecode(path.value) == self.executable:
                process = self.inspect(pid)
                if process is not None: found.append(process)
            elif name.value.startswith(b"Sid Meiers Rail"):
                raise SafetyError("Another or unidentified Railroads process is running")
        return found

    def stop(self, process):
        for sig, seconds in ((signal.SIGTERM, 2), (signal.SIGKILL, 3)):
            current = self.inspect(process.pid)
            if current is None: return
            if not process.same(current): raise SafetyError("PID identity changed; refusing to signal")
            try:
                os.kill(process.pid, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + seconds
            unreadable = None
            while time.monotonic() < deadline:
                try:
                    current = self.inspect(process.pid)
                except SafetyError as exc:
                    # During termination macOS can retain the PID briefly after
                    # its executable path disappears. Wait for independent exit
                    # evidence, without sending another signal to an unknown PID.
                    unreadable = exc
                    time.sleep(0.1)
                    continue
                if current is None: return
                if not process.same(current): raise SafetyError("PID was reused; refusing further signals")
                unreadable = None
                time.sleep(0.1)
            if unreadable is not None: raise unreadable
        raise SafetyError("Test game did not stop; profiles retained")

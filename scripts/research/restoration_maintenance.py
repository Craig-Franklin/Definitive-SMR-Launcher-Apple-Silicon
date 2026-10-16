"""Separately admitted filesystem maintenance after a held finite attempt.

No game launch, input, signal, recovery replay, lease release or old-journal write.
An expired experiment never supplies maintenance authorization. The caller must
install Craig's separate, reviewed, time-bounded approval under the STATE lock.
This is a cooperative same-user protocol, not authentication against that user.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time

REPOSITORY = Path(__file__).resolve().parents[2]
# Ordinary CLI invocation must select this checkout instead of an editable
# installation elsewhere. Preloaded foreign modules still fail the origin gate.
sys.path.insert(0, str(REPOSITORY / "src"))

from active_job_control import decode, read_plain, require, sha
from runner_journal import write_once
from smr_launcher.activation import (
    _assert_no_symlink_ancestor,
    _assert_regular_tree,
    _fsync_dir,
)
from smr_launcher.resource_identity import fingerprint_resources
from smr_launcher.live_run_authority import ObservedIO, SettingsPreimage
from smr_launcher.variants import _tree_manifest

SCOPE = "RESTORATION_MAINTENANCE_ONLY_V1"
MAX_SECONDS = 300


def verify_imported_sources(job):
    """Reject editable-install imports from another checkout before discovery."""
    repository = REPOSITORY
    closure = {row["path"] for row in job["closure"]}
    for name, module in tuple(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if filename is None or name == __name__:
            continue
        path = Path(filename).resolve()
        if name == "smr_launcher" or name.startswith("smr_launcher."):
            require(
                path.is_relative_to(repository / "src/smr_launcher") and str(path) in closure,
                "Imported maintained source outside sealed checkout",
            )
        elif "/scripts/research/" in str(path):
            require(str(path) in closure, "Imported research source outside sealed closure")
    for name, expected in {
        "game_queue": Path(job["queue_module"]),
        "frozen_mac_processes": Path(job["common_root"]) / "stop-helper/frozen_mac_processes.py",
    }.items():
        require(
            name in sys.modules
            and Path(sys.modules[name].__file__).resolve() == expected
            and str(expected) in closure,
            "Imported frozen dependency outside sealed closure",
        )


class MaintenanceIO(ObservedIO):
    """Fence maintained activation calls at return and before the exchange syscall."""

    def __init__(self, operation):
        self.operation = operation

    def call(self, name, function, *args, **kwargs):
        # Lock cleanup always remains available after a deadline/refusal.
        if name in {"activation.lock_release", "activation.lock_close", "activation.lock_exit"}:
            return function(*args, **kwargs)
        self.operation.stopped()
        self.operation.deadline()
        result = function(*args, **kwargs)
        if name == "activation.lock_open":
            # Transfer the descriptor to the backend's try/finally first. The
            # next lock-acquire call checks time before acquiring the lock;
            # raising here would strand a descriptor the caller never received.
            return result
        self.operation.deadline()
        return result

    def atomic_json(self, path, value):
        return self.call("maintenance.atomic_json", super().atomic_json, path, value)

    def fsync_dir(self, path):
        return self.call("maintenance.fsync_dir", super().fsync_dir, path)

    def fsync_tree(self, path):
        return self.call("maintenance.fsync_tree", super().fsync_tree, path)

    def readbytes(self, path):
        captured = self.call("maintenance.readbytes", SettingsPreimage.capture, path, 64 * 1024**2)
        require(captured.kind == "file", "Plain bounded activation asset required")
        return captured.data


def tree_snapshot(path):
    """Match the original owner's content, mode and nanosecond-mtime baseline."""
    path = Path(path)
    _assert_no_symlink_ancestor(path)
    _assert_regular_tree(path)
    rows, total = [], 0
    for base, directories, files in os.walk(path, followlinks=False):
        for name in sorted(directories + files):
            child = Path(base) / name
            info = child.lstat()
            require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), "Plain tree required")
            total += info.st_size if child.is_file() else 0
            require(len(rows) < 25000 and total <= 2 * 1024**3, "Preservation budget exceeded")
            rows.append(
                {
                    "path": str(child.relative_to(path)),
                    "mode": stat.S_IMODE(info.st_mode),
                    "mtime_ns": info.st_mtime_ns,
                    "bytes": info.st_size if child.is_file() else None,
                }
            )
    return {"manifest": json.loads(json.dumps(_tree_manifest(path))), "metadata": rows}


def file_snapshot(path):
    before = Path(path).stat(follow_symlinks=False)
    data = read_plain(path, maximum=262144)
    info = Path(path).stat(follow_symlinks=False)
    require(
        before == info and info.st_nlink == 1 and stat.S_ISREG(info.st_mode),
        "Plain unchanged preference required",
    )
    return {"sha256": sha(data), "mode": stat.S_IMODE(info.st_mode), "mtime_ns": info.st_mtime_ns}


def pinned(row):
    data = read_plain(row["path"])
    require(len(data) == row["bytes"] and sha(data) == row["sha256"], "Maintenance input changed")
    return data


def validate_completion(plan):
    """Consume positive complete-response evidence, never infer click success.

    The independently reviewed plan pins the exact response and matching native
    single-click synchronous branch. Timing omitted by the old owner stays absent.
    This predicate only settles provider emission for filesystem maintenance.
    """
    evidence = plan["completion"]
    response = decode(pinned(evidence["response"]))
    created = decode(pinned(evidence["created"]))
    exited = decode(pinned(evidence["exited"]))
    require(
        response == evidence["expected_response"]
        and response["ok"] is False
        and response["error"]["code"] == "window_not_focused"
        and "; 1 press(es) may already have been delivered;" in response["error"]["message"]
        and response["_meta"] == {"runtimeId": plan["provider_runtime_id"]}
        and created["mutable"] is True
        and created["pid"] == exited["pid"] == evidence["input_pid"]
        and exited["reaped"] is True
        and exited["owned_wait_returncode"] == 1,
        "Positive completed single-click response required",
    )


class RestorationMaintenance:
    """One bounded transaction; constructor performs no filesystem mutation.

    app/monitor/control/queue are the existing maintained or frozen dependencies.
    Synthetic providers are explicit in tests; they establish no runtime result.
    """

    def __init__(
        self,
        plan_path,
        approval_path,
        approval_sha,
        app,
        monitor,
        control,
        queue,
        *,
        clock=time.monotonic,
        epoch=time.time,
    ):
        self.plan_path, self.approval_path = Path(plan_path), Path(approval_path)
        self.plan_raw = read_plain(self.plan_path)
        self.plan = decode(self.plan_raw)
        self.approval_raw = read_plain(approval_path)
        self.approval = decode(self.approval_raw)
        self.app, self.monitor, self.control, self.queue = app, monitor, control, queue
        self.clock, self.epoch = clock, epoch
        self.started = clock()
        self.last = self.started
        a, p = self.approval, self.plan
        require(
            sha(self.approval_raw) == approval_sha
            and a["scope"] == p["scope"] == SCOPE
            and a["approved_by"] == "Craig"
            and a["plan_sha256"] == sha(self.plan_raw)
            and a["root"] == p["root"]
            and a["original_job_sha256"] == p["original_job"]["sha256"]
            and a["candidate_sha256"] == sha(read_plain(Path(__file__).resolve()))
            and type(a["issued_epoch"]) in (int, float)
            and type(a["expires_epoch"]) in (int, float)
            and type(a["issued_monotonic"]) in (int, float)
            and type(a["expires_monotonic"]) in (int, float)
            and 0 < a["expires_epoch"] - a["issued_epoch"] <= MAX_SECONDS
            and 0 < a["expires_monotonic"] - a["issued_monotonic"] <= MAX_SECONDS
            and a["issued_epoch"] <= epoch() < a["expires_epoch"],
            "Separate exact Craig maintenance approval required",
        )
        require(
            a["issued_monotonic"] <= self.started < a["expires_monotonic"],
            "Separate fixed monotonic maintenance admission required",
        )
        review = decode(pinned(a["review"]))
        require(
            review["decision"] == "GO_SOURCE_ONLY_RESTORATION_MAINTENANCE"
            and review["plan_sha256"] == sha(self.plan_raw)
            and review["candidate_sha256"] == a["candidate_sha256"]
            and review["findings"] == [],
            "Exact independent maintenance review required",
        )
        job = decode(pinned(p["original_job"]))
        require(
            job == control.job
            and job["root"] == p["root"]
            and job["return_profile"] == p["return_profile"]
            and "map-" + job["diagnostic_variant"] == p["diagnostic_profile"]
            and job["preservation"] == p["baseline"],
            "Original complete baseline correspondence required",
        )
        self.job = job
        original_admission = decode(pinned(p["original_admission"]))
        require(
            original_admission["lease"] == p["lease"]
            and original_admission["queue_path"] == p["queue_path"],
            "Original lease changed",
        )
        positive = decode(pinned(p["positive_exits"]))
        controller = decode(pinned(p["controller"]))
        created = decode(pinned(p["completion"]["created"]))
        require(
            created["parent_pid"] == controller["pid"]
            and positive["game_absent"] is True
            and {row["role"] for row in positive["children"]} == {"game", "observer"}
            and all(row["positive_exit"] is True for row in positive["children"]),
            "Exact owned parent/game/observer stop receipts required",
        )
        require(
            set(p["owned_pids"])
            == {
                controller["pid"],
                p["completion"]["input_pid"],
                *(row["pid"] for row in positive["children"]),
            },
            "Complete owned process absence set required",
        )
        original_preferences = decode(pinned(p["original_preferences"]))
        require(
            len(p["preferences"]) == len(original_preferences) == 2
            and all(
                {**row["baseline"], "path": row["path"], "copy": row["copy"]} == old
                for row, old in zip(p["preferences"], original_preferences)
            ),
            "Original preference correspondence required",
        )
        require(app.installation.executable_sha256 == job["game_sha256"], "Game build changed")
        (record,) = [
            r for r in app.catalogue() if r.variant_id == job["source_original"]["variant_id"]
        ]
        require(
            p["protected_paths"]
            == {
                "canonical": str(app.profiles._profile_path(job["source_profile"])),
                "import": str(app.imports / record.imported_directory),
                "prepared": str(app.prepared / record.prepared_directory),
                "stock": str(app.profiles._profile_path("original-game")),
            }
            and p["installed_assets"]
            == str(
                app.installation.steamapps_root
                / "common/Sid Meier's Railroads/SMRailroadsData/assets"
            ),
            "Application paths changed",
        )
        self.result = Path(p["result_directory"])
        require(
            not self.result.exists() and not self.result.is_symlink(), "One-use result required"
        )
        self.switched = False

    def deadline(self):
        now = self.clock()
        require(
            type(now) in (int, float)
            and self.last <= now < self.approval["expires_monotonic"]
            and self.approval["issued_epoch"] <= self.epoch() < self.approval["expires_epoch"],
            "Separate maintenance deadline expired or clock invalid",
        )
        self.last = now

    def immutable(self):
        self.deadline()
        require(
            read_plain(self.plan_path) == self.plan_raw
            and read_plain(self.approval_path) == self.approval_raw
            and sha(read_plain(Path(__file__).resolve())) == self.approval["candidate_sha256"],
            "Maintenance plan, approval or candidate changed",
        )
        pinned(self.approval["review"])
        pinned(self.plan["original_job"])
        for key in ("original_admission", "controller", "original_preferences"):
            pinned(self.plan[key])
        for row in self.job["closure"] + self.plan["evidence"]:
            pinned(row)
        validate_completion(self.plan)
        require(
            decode(pinned(self.plan["original_outcome"])) == self.plan["expected_outcome"]
            and self.plan["expected_outcome"]["payload"]["status"] == "automation_failed"
            and self.plan["expected_outcome"]["phase"] == "raw_outcome"
            and self.plan["expected_outcome"]["run_id"] == self.job["job"]
            and decode(pinned(self.plan["positive_exits"]))["uncertain_input"] is True,
            "Original failed UNKNOWN evidence required",
        )

    def stopped(self):
        self.deadline()
        require(
            self.app.installation.game_running() is False and not self.monitor.games(),
            "No live or foreign game permitted",
        )
        for pid in self.plan["owned_pids"]:
            require(
                type(pid) is int and pid > 0 and self.monitor.inspect(pid) is None,
                "Owned request or process still present",
            )
        require(
            self.plan["completion"]["input_pid"] in self.plan["owned_pids"],
            "Input child absence missing",
        )

    @contextmanager
    def guard(self):
        with self.control.cleanup_guard():
            state = decode(read_plain(self.control.path))
            require(
                state.get("restoration_maintenance") == self.approval,
                "Separate exclusive maintenance ownership changed",
            )
            self.queue.guard(self.plan["lease"])
            self.stopped()
            self.immutable()
            require(
                not self.app.profiles.journal.exists()
                and not self.app.profiles.journal.is_symlink(),
                "Pending activation recovery",
            )
            require(
                not (self.app.library / "removal.json").exists()
                and not (self.app.library / "removal.json").is_symlink(),
                "Pending removal",
            )
            self.preservation(restored=self.switched)
            self.stopped()
            self.deadline()
            yield
            self.deadline()

    def preservation(self, *, restored):
        p, manager = self.plan, self.app.profiles
        require(
            manager._state()["active"]
            == (p["return_profile"] if restored else p["diagnostic_profile"]),
            "Active profile changed",
        )
        return_path = manager.live if restored else manager._profile_path(p["return_profile"])
        require(tree_snapshot(return_path) == p["baseline"]["live"], "Return profile changed")
        diagnostic = manager._profile_path(p["diagnostic_profile"]) if restored else manager.live
        require(
            tree_snapshot(diagnostic) == p["diagnostic_snapshot"], "Diagnostic contents changed"
        )
        for role, path in p["protected_paths"].items():
            require(tree_snapshot(path) == p["baseline"][role], "Protected input changed: " + role)
        require(
            set(p["protected_paths"]) == {"canonical", "import", "prepared", "stock"},
            "Complete protected paths required",
        )
        require(
            fingerprint_resources({"installed_assets": Path(p["installed_assets"])})["identity"]
            == self.job["installed_assets_identity"],
            "Installed dependencies changed",
        )

    def preferences(self, *, restored):
        for row in self.plan["preferences"]:
            require(file_snapshot(row["copy"]) == row["baseline"], "Preference preimage changed")
            require(
                Path(row["path"]).name == "Preferences Data"
                or row["current"]["sha256"] == row["baseline"]["sha256"],
                "Unexpected global preference bytes changed",
            )
            require(
                file_snapshot(row["path"]) == (row["baseline"] if restored else row["current"]),
                "Preference correspondence changed",
            )

    def run(self):
        # App/profile locks precede the STATE guard. No game/provider waits occur
        # inside it. A failed transaction stays held; this object never retries.
        with self.app._locked(recover_removal=False, nonblocking=True):
            self.app._verify_installation_binding()
            with self.guard():
                self.preservation(restored=False)
                self.preferences(restored=False)
                self.result.mkdir(mode=0o700)
                write_once(
                    self.result / "started.json",
                    {
                        "scope": SCOPE,
                        "plan_sha256": sha(self.plan_raw),
                        "original_job_sha256": self.plan["original_job"]["sha256"],
                        "original_effect": "unknown",
                        "original_uncertain_input": True,
                    },
                )
            manager = self.app.profiles
            require(manager.observed_io is None, "Unexpected activation observer")
            manager.observed_io = MaintenanceIO(self)
            try:
                manager.switch(self.plan["return_profile"], mutation_guard=self.guard)
            finally:
                manager.observed_io = None
            self.switched = True
            for index, row in enumerate(self.plan["preferences"]):
                path = Path(row["path"])
                with self.guard():
                    require(
                        file_snapshot(path) == row["current"], "Preference changed before restore"
                    )
                    shutil.copy2(path, self.result / ("preference-post-" + str(index)))
                    if row["current"] != row["baseline"]:
                        require(
                            path.name == "Preferences Data"
                            or row["current"]["sha256"] == row["baseline"]["sha256"],
                            "Unexpected global preference bytes changed",
                        )
                        temporary = path.with_name(path.name + ".restoration-maintenance")
                        require(
                            not temporary.exists() and not temporary.is_symlink(),
                            "Preference restoration collision",
                        )
                        shutil.copy2(row["copy"], temporary)
                        with temporary.open("rb") as stream:
                            os.fsync(stream.fileno())
                        require(
                            file_snapshot(temporary) == row["baseline"], "Restoration copy mismatch"
                        )
                        require(
                            file_snapshot(path) == row["current"], "Preference changed during copy"
                        )
                        self.stopped()
                        os.replace(temporary, path)
                        _fsync_dir(path.parent)
            with self.guard():
                self.preservation(restored=True)
                self.preferences(restored=True)
                write_once(
                    self.result / "restored.json",
                    {
                        "scope": SCOPE,
                        "plan_sha256": sha(self.plan_raw),
                        "restored": True,
                        "original_job_sha256": self.plan["original_job"]["sha256"],
                        "original_effect": "unknown",
                        "original_uncertain_input": True,
                        "original_run_restored": False,
                        "lease_released": False,
                        "automatic_successor": False,
                        "elapsed_seconds": self.clock() - self.started,
                    },
                )
        return self.result / "restored.json"


def main():
    """Explicit maintenance invocation; missing approval refuses before locking."""
    require(sys.version_info[:2] == (3, 12) and not sys.flags.optimize, "Python 3.12 required")
    require(len(sys.argv) == 4, "plan-path approval-path approval-sha256 required")
    plan = decode(read_plain(sys.argv[1]))
    approval_raw = read_plain(sys.argv[2])
    require(sha(approval_raw) == sys.argv[3], "Explicit maintenance approval pin required")
    approval = decode(approval_raw)
    require(
        approval.get("approved_by") == "Craig"
        and approval.get("scope") == SCOPE
        and approval.get("plan_sha256") == sha(read_plain(sys.argv[1])),
        "Separate maintenance approval required",
    )
    job = decode(pinned(plan["original_job"]))
    for row in job["closure"]:
        pinned(row)
    from agent_actor_binding import collect_actor
    from agent_driver import AgentJobControl
    from smr_launcher.application import LauncherApplication

    actor = collect_actor(job["actor_binding"])
    require(
        actor["model"] == "gpt-6.1-sol"
        and actor["effort"] == "high"
        and actor["approval_policy"] == "never"
        and actor["sandbox"] == "danger-full-access",
        "Actual maintenance owner provenance changed",
    )
    sys.path[:0] = [
        str(Path(job["common_root"]) / "stop-helper"),
        str(Path(job["queue_module"]).parent),
    ]
    from frozen_mac_processes import MacProcesses
    from game_queue import GameQueue

    verify_imported_sources(job)
    app = LauncherApplication.discover()
    operation = RestorationMaintenance(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3],
        app,
        MacProcesses(str(app.installation.executable)),
        AgentJobControl(job),
        GameQueue(plan["queue_path"]),
    )
    verify_imported_sources(job)
    print(json.dumps({"maintenance_readback": str(operation.run())}, sort_keys=True))


if __name__ == "__main__":
    main()

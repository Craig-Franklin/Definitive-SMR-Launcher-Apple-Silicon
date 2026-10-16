"""One finite packaged-backend Play/menu trial, using the existing driver owner.

This deliberately separate catalogue path never creates or registers a profile.
Its three declared mutable files are Settings.ini and the two game preferences.
Existing saves/assets remain exact; newly created files stay in their own edition.
"""

from contextlib import contextmanager, nullcontext
from dataclasses import asdict
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import stat
import subprocess
import sys
from threading import Thread
import time
import uuid

from active_job_control import decode, read_plain, require, sha
from smr_launcher.activation import _assert_no_symlink_ancestor, _assert_regular_tree, _fsync_dir
from smr_launcher.packages import _digest
from smr_launcher.scenario_evidence import canonical
from unique_save_file import signature

PURPOSE = "ONE_AGENT_PACKAGED_CATALOGUE_MENU_V1"
CACHED_PURPOSE = "ONE_AGENT_PACKAGED_CACHED_MENU_V1"
LOAD_PURPOSE = "ONE_AGENT_PACKAGED_CACHED_LOAD_V1"
SAVE_PURPOSE = "ONE_AGENT_PACKAGED_CACHED_SAVE_REOPEN_V1"
OWNED_AUTOSAVE = "Saves/Auto0.SMRailroadsSavedGame"


class MenuIncomplete(ValueError):
    """Reserve the original quit interval instead of more menu exploration."""


def file_facts(path):
    """Content and stable file metadata, excluding access/rename-sensitive ctime."""
    path = Path(path)
    _assert_no_symlink_ancestor(path)
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 2 * 1024**3,
        "Bounded plain preservation file required",
    )
    digest = _digest(path)
    require(signature(path.lstat()) == signature(info), "Preservation file changed while hashing")
    return dict(
        sha256=digest,
        bytes=info.st_size,
        inode=info.st_ino,
        mode=stat.S_IMODE(info.st_mode),
        mtime_ns=info.st_mtime_ns,
    )


def tree_files(root):
    root = Path(root)
    _assert_regular_tree(root)
    result = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_file():
            facts = file_facts(path)
            total += facts["bytes"]
            require(len(result) < 25000 and total <= 2 * 1024**3, "Preservation bound exceeded")
            result[path.relative_to(root).as_posix()] = facts
    return result


def existing_target_unchanged(root, before):
    """Allow new files and log changes, never changed/deleted pre-existing saves."""
    actual = tree_files(root)
    require(
        all(
            actual.get(name) == facts
            for name, facts in before.items()
            if name != "Settings.ini" and not name.startswith("Logs/")
        ),
        "Existing target file changed; preserve for inspection",
    )
    return actual


def autosave_origin(job):
    """Accept the exact owned autosave from an independently settled trial."""
    config = job["catalogue_menu"]
    proof = config["owned_autosave"]
    require(
        set(proof) == {"origin_readback", "origin_sha256", "facts"},
        "Exact autosave origin required",
    )
    raw = read_plain(proof["origin_readback"])
    require(sha(raw) == proof["origin_sha256"], "Owned autosave origin changed")
    origin = decode(raw)
    service = origin["service_settled"]
    if type(service) is list:
        # A failed second-launch attempt does not invalidate the first game's
        # observed normal exit or the root's preservation/lease settlement.
        require(len(service) == 2, "Exact settled two-service origin required")
        first, second = service
        exit_result = origin["exact_normal_exit"]["result"]
        response = second["commit_response"]
        observed = origin.get("second_observer", {})
        pid = observed.get("process", {}).get("pid")
        second_owned_exit = (
            second["commit_sent"] is True
            and type(response) is dict
            and response.get("ok") is True
            and response["data"]["launched_profile_id"] == config["target"]["profile_id"]
            and type(origin.get("job")) is str
            and observed.get("job") == origin["job"]
            and type(pid) is int
            and pid > 0
            and pid != origin["exact_normal_exit"].get("process", {}).get("pid")
            and observed.get("result", {}).get("state") == "observed_exited"
            and observed["result"]["terminal"] is True
            and any(
                row.get("pid") == pid
                and row.get("role") == "game"
                and row.get("positive_absence") is True
                for row in origin.get("owned_absences", [])
            )
        )
        completed = (
            origin["invocation"]["returncode"] == 2
            and first["commit_sent"] is True
            and first["commit_response"]["ok"] is True
            and ((second["commit_sent"] is False and response is None) or second_owned_exit)
            and all(
                row["reaped"] is True
                and row["returncode"] == 0
                and row["pending"] is None
                and row["eof_closed"] is True
                for row in service
            )
            and exit_result["state"] == "observed_exited"
            and exit_result["stop_requested"] is False
            and exit_result["terminal"] is True
        )
        facts = origin["current_test_autosave"]
        service = first
    else:
        completed = origin["invocation"]["returncode"] == 0
        facts = origin["new_target_files_retained"].get(OWNED_AUTOSAVE)
    require(
        origin["lease_released"] is True
        and origin["preservation_verified"] is True
        and origin["terminal_finished"] is True
        and origin["game_absent"] is True
        and service["reaped"] is True
        and completed
        and service["commit_response"]["data"]["launched_profile_id"]
        == config["target"]["profile_id"]
        and facts == proof["facts"]
        and config["preservation"]["target_files"].get(OWNED_AUTOSAVE) == proof["facts"]
        and 0 < proof["facts"]["bytes"] <= 512 * 1024**2,
        "Autosave must be the exact retained new file from a settled owned trial",
    )
    return proof


def target_files_unchanged(owner, root):
    before = owner.baseline["target_files"]
    archive = getattr(owner, "autosave_preimage", None)
    if owner.job.get("purpose") == SAVE_PURPOSE and archive is not None:
        proof = autosave_origin(owner.job)
        actual = file_facts(archive)
        require(
            all(actual[k] == proof["facts"][k] for k in ("sha256", "bytes", "mode", "mtime_ns")),
            "Owned autosave preimage changed; preserve for inspection",
        )
        before = {name: facts for name, facts in before.items() if name != OWNED_AUTOSAVE}
    return existing_target_unchanged(root, before)


def preserve_owned_autosave(owner, root):
    proof = autosave_origin(owner.job)
    source = root / OWNED_AUTOSAVE
    copy = owner.directory / "owned-Auto0-preimage"
    require(not copy.exists() and not copy.is_symlink(), "Autosave preimage collision")
    require(file_facts(source) == proof["facts"], "Owned autosave changed before preservation")
    shutil.copy2(source, copy)
    with copy.open("r+b") as stream:
        os.fsync(stream.fileno())
    _fsync_dir(copy.parent)
    require(file_facts(source) == proof["facts"], "Owned autosave changed while preserving")
    copied = file_facts(copy)
    require(
        all(copied[k] == proof["facts"][k] for k in ("sha256", "bytes", "mode", "mtime_ns")),
        "Owned autosave preimage differs",
    )
    return copy


def sibling_saves(app, target_id, return_id):
    return {
        record.profile_id: tree_files(app.profiles._profile_path(record.profile_id) / "Saves")
        for record in app.catalogue()
        if record.profile_id not in (target_id, return_id)
    }


def verify_sibling_saves(owner):
    current = sibling_saves(
        owner.app, owner.config["target"]["profile_id"], owner.job["return_profile"]
    )
    require(
        all(
            profile in current
            and all(current[profile].get(name) == facts for name, facts in rows.items())
            for profile, rows in owner.baseline["sibling_saves"].items()
        ),
        "Existing sibling save changed; preserve for inspection",
    )


def verify_config(job):
    config = job["catalogue_menu"]
    target = config["target"]
    require(
        job["purpose"] in (PURPOSE, CACHED_PURPOSE, LOAD_PURPOSE, SAVE_PURPOSE),
        "Closed catalogue purpose required",
    )
    cached = job["purpose"] in (CACHED_PURPOSE, LOAD_PURPOSE, SAVE_PURPOSE)
    if job["purpose"] in (LOAD_PURPOSE, SAVE_PURPOSE):
        title = config.get("scenario_title")
        require(
            type(title) is str
            and 0 < len(title) <= 256
            and title.strip() == title
            and all(ord(c) >= 32 and ord(c) != 127 for c in title),
            "Exact reviewed rendered scenario title required",
        )
        saves = {
            name for name in config["preservation"]["target_files"] if name.startswith("Saves/")
        }
        if job["purpose"] == SAVE_PURPOSE:
            require(
                OWNED_AUTOSAVE in saves
                and not saves.intersection(
                    f"Saves/Auto{i}.SMRailroadsSavedGame" for i in range(1, 5)
                ),
                "Only the proven owned autosave may rotate; pretrial Auto1..4 refuse",
            )
            autosave_origin(job)
        else:
            require(not saves, "First scenario load requires a target without pre-existing saves")
    require(
        set(target) == {"profile_id", "archive_sha256", "variant_id", "scenarios", "assets_sha256"},
        "Exact catalogue target fields required",
    )
    require(
        target["profile_id"] == "map-" + target["variant_id"]
        and target["profile_id"] != job["return_profile"]
        and len(target["scenarios"]) == 1,
        "Distinct single-scenario edition required",
    )
    require(
        config["prepare_compatibility"] is cached
        and type(config["warning"]) is str
        and (0 < len(config["warning"]) <= 16384 if cached else config["warning"] == ""),
        "Exact direct or cached preparation/warning contract required",
    )
    if cached:
        selection = config["selection"]
        require(
            set(selection) == {"profile_id", "archive_sha256", "variant_id", "scenarios"}
            and selection["profile_id"] == "map-" + selection["variant_id"]
            and selection["profile_id"] not in (target["profile_id"], job["return_profile"])
            and selection["archive_sha256"] == target["archive_sha256"]
            and selection["scenarios"] == target["scenarios"]
            and config["preparation"]["cached_profile_id"] == target["profile_id"],
            "Exact original-to-cached selection required",
        )
    for key in ("archive_sha256", "variant_id", "assets_sha256"):
        require(
            len(target[key]) == 64 and all(c in "0123456789abcdef" for c in target[key]),
            "Exact target digest required",
        )
    source = job["source_original"]
    require(
        source["archive_sha256"] == target["archive_sha256"]
        and source["variant_id"] == target["variant_id"]
        and source["scenario"] == target["scenarios"][0],
        "Journal target correspondence required",
    )
    package = config["package"]
    receipt = decode(read_plain(package["receipt_path"]))
    require(
        sha(read_plain(package["receipt_path"])) == package["receipt_sha256"]
        and receipt["source_commit"] == package["source_commit"]
        and receipt["tag"] == package["tag"]
        and receipt["zip_sha256"] == package["zip_sha256"]
        and receipt["codesign_verified"] is True
        and receipt["gatekeeper_accepted"] is True,
        "Exact released artifact required",
    )
    bundle = Path(package["bundle"])
    helper = bundle / "Contents/MacOS/Definitive SMR Launcher Apple Silicon"
    require(
        str(helper) == package["executable"]
        and sha(read_plain(helper, maximum=128 * 1024**2)) == package["executable_sha256"],
        "Exact packaged backend helper required",
    )
    return config


class PackagedService:
    """Closed stdio transport for this single Play, with owned EOF/reap evidence."""

    def __init__(self, owner):
        self.owner = owner
        self.pending = None
        self.commit_sent = False
        self.commit_response = None
        self.closed = False
        self.eof = False
        self.messages = Queue(maxsize=128)
        self.reader_error = None
        self.stderr = (owner.directory / "service-stderr.txt").open("xb")
        self.stdout = (owner.directory / "service-stdout.jsonl").open("xb")
        env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME"):
            env.pop(key, None)
        with owner.mutation_guard():
            self.child = subprocess.Popen(
                [owner.config["package"]["executable"], "--native-service"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.stderr,
                env=env,
                cwd=owner.directory,
                start_new_session=True,
                bufsize=0,
            )
        from frozen_mac_processes import MacProcesses

        self.monitor = MacProcesses(owner.config["package"]["executable"])
        self.identity = self.monitor.inspect(self.child.pid)
        if self.identity is None:
            # No request has been sent. Settle our direct child on failed setup.
            self.child.stdin.close()
            self.child.terminate()
            self.child.wait(timeout=3)
            self.stdout.close()
            self.stderr.close()
            raise ValueError("Packaged service identity unavailable")
        owner.creation.append(dict(role="packaged_service", **asdict(self.identity)))
        os.set_blocking(self.child.stdin.fileno(), False)
        self.thread = Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        total = 0
        try:
            while True:
                line = self.child.stdout.readline(96 * 1024**2 + 1)
                if not line:
                    self.eof = True
                    break
                total += len(line)
                require(
                    len(line) <= 96 * 1024**2 and total <= 128 * 1024**2,
                    "Packaged response size bound",
                )
                self.stdout.write(line)
                self.stdout.flush()
                self.messages.put_nowait(decode(line))
        except BaseException as exc:
            self.reader_error = str(exc)

    def send(self, ident, method, params, *, commit=False):
        require(
            self.pending is None and not self.closed and self.child.poll() is None,
            "Packaged request pending or service closed",
        )
        require(not commit or not self.commit_sent, "One packaged Play commit maximum")
        raw = canonical(dict(id=ident, method=method, params=params)) + b"\n"
        require(len(raw) <= 4096, "Bounded packaged request required")
        with self.owner.mutation_guard():
            self.owner.control._poll_locked()
            validator = getattr(self.owner, "delivery_validation", None)
            if commit and validator is not None:
                validator()
            require(not self.owner.control.cancelled, "Cancelled before packaged request")
            require(time.monotonic() < self.owner.launch_deadline, "Packaged launch cutoff expired")
            # This private pipe has one writer. Record uncertainty before its
            # single nonblocking write; an incomplete write is never replayed.
            self.pending = ident
            if commit:
                self.commit_sent = True
                self.owner.open_attempted = True
            require(
                os.write(self.child.stdin.fileno(), raw) == len(raw),
                "Incomplete packaged request; do not replay",
            )

    def receive(self, *, event=None, deadline):
        while time.monotonic() < deadline:
            self.owner.tick()
            require(self.reader_error is None, "Packaged reader failed: " + str(self.reader_error))
            try:
                value = self.messages.get(timeout=0.05)
            except Empty:
                require(not self.eof, "Packaged EOF before response")
                continue
            if value.get("event") == "closed":
                self.closed = True
            if event is not None and value.get("event") == event:
                return value
            if self.pending is not None and value.get("id") == self.pending:
                if self.commit_sent and self.pending == "play-commit":
                    self.commit_response = value
                self.pending = None
                return value
        raise ValueError("Original packaged request deadline expired")

    def settle(self):
        """EOF fences any undispatched work. Reap before releasing observer authority."""
        if not self.child.stdin.closed:
            self.child.stdin.close()
        limit = min(self.owner.anchor + 900, time.monotonic() + 20)
        while self.child.poll() is None and time.monotonic() < limit:
            self.owner.verify_cleanup_owner()
            time.sleep(0.05)
        if self.child.poll() is None:
            with self.owner.control.cleanup_guard():
                current = self.monitor.inspect(self.child.pid)
                require(self.identity.same(current), "Packaged service identity changed")
                self.child.terminate()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with self.owner.control.cleanup_guard():
                    require(
                        self.identity.same(self.monitor.inspect(self.child.pid)),
                        "Packaged service identity changed before kill",
                    )
                    self.child.kill()
                self.child.wait(timeout=2)
        self.child.wait()
        self.thread.join(timeout=2)
        require(not self.thread.is_alive(), "Packaged reader unsettled")
        self.child.stdout.close()
        while not self.messages.empty():
            value = self.messages.get_nowait()
            if value.get("event") == "closed":
                self.closed = True
            if self.pending is not None and value.get("id") == self.pending:
                if self.pending == "play-commit":
                    self.commit_response = value
                self.pending = None
        require(self.monitor.inspect(self.child.pid) is None, "Packaged service absence unproven")
        for stream in (self.stdout, self.stderr):
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        self.owner.write(
            "service-settled.json",
            dict(
                pid=self.child.pid,
                returncode=self.child.returncode,
                reaped=True,
                eof_closed=self.closed,
                pending=self.pending,
                commit_sent=self.commit_sent,
                commit_response=self.commit_response,
            ),
        )


def arm_observer(owner, common):
    """Reuse the frozen observer's admission, clock, process and sample contracts."""
    from frozen_mac_processes import MacProcesses
    from wrapper import bounded

    helper = owner.root / "stop-helper"
    owner.code = sha(read_plain(helper / "pins.json"))
    owner.nonce = uuid.uuid4().hex
    admission = dict(
        job=owner.job["job"],
        nonce=owner.nonce,
        code=owner.code,
        anchor=owner.anchor,
        expected_executable=str(owner.app.installation.executable),
        interpreter=sys.executable,
        clock_domain="root-venv-time.monotonic-v1",
    )
    owner.publish(
        "admission.json",
        dict(
            admission=admission,
            approval=dict(
                issue=127,
                status="approved",
                proposal_sha256=decode(read_plain(helper / "proposal.json"))["sha256"],
                job=owner.job["job"],
                nonce=owner.nonce,
                code=owner.code,
                anchor=owner.anchor,
            ),
            source_acceptance=dict(
                issue=129,
                status="accepted",
                observer_sha256="8683b919f8d3bd0b68675e6c6731839144a7c68555a75efafab8309ca18b19b3",
                component_sha256="c7a4a0eee7e2ceb2cf900eeabcc93689119c75e8f61fe96bac933c5247079848",
            ),
        ),
    )
    owner.helper_log = (owner.directory / "stop-observer.log").open("xb")
    before = time.monotonic()
    with owner.mutation_guard():
        owner.observer = subprocess.Popen(
            [sys.executable, "-B", str(helper / "agent_host.py"), str(owner.helper_packet)],
            stdout=owner.helper_log,
            stderr=subprocess.STDOUT,
        )
    limit = min(before + 5, owner.launch_deadline)
    while not (owner.helper_packet / "ready.json").exists():
        owner.tick()
        require(time.monotonic() < limit, "Stop observer failed to arm")
        time.sleep(0.05)
    identity = MacProcesses(sys.executable).inspect(owner.observer.pid)
    require(identity is not None, "Observer identity unavailable")
    owner.creation.append(dict(role="observer", **asdict(identity)))
    bootstrap = decode(bounded(owner.helper_packet / "bootstrap.json"))
    common.verify_observer_bootstrap(
        bootstrap,
        asdict(identity),
        owner.controller_identity["executable"],
        sys.executable,
        sys.prefix,
        before,
        time.monotonic(),
        vars(time.get_clock_info("monotonic")),
    )
    owner.write("helper-clock-binding.json", bootstrap)
    return admission


def bind_observer(owner, admission):
    from sample_fence import _wait_initial_sample_tick
    from wrapper import bounded

    limit = min(owner.request + 20, owner.launch_deadline)
    while owner.game is None:
        owner.tick()
        require(time.monotonic() < limit, "Packaged launch identity unresolved")
        time.sleep(0.05)
    owner.sample(force=True)
    process = dict(
        pid=owner.game.pid, birth=str(owner.game.birth_us), executable=owner.game.executable
    )
    ready = decode(bounded(owner.helper_packet / "ready.json"))
    _wait_initial_sample_tick(
        owner.helper_packet, admission, process, ready, limit, owner.verify_available
    )
    owner.publish(
        "descriptor.json",
        dict(
            version=1,
            job=owner.job["job"],
            nonce=owner.nonce,
            code=owner.code,
            anchor=owner.anchor,
            request=owner.request,
            process=process,
        ),
    )
    while not (owner.helper_packet / "bound.json").exists():
        owner.verify_available()
        require(
            owner.game.same(owner.monitor.inspect(owner.game.pid))
            and owner.observer.poll() is None
            and time.monotonic() < limit,
            "Observer binding unresolved or process changed",
        )
        time.sleep(0.05)
    bound = decode(bounded(owner.helper_packet / "bound.json"))
    require(
        bound["state"]["bound"] is True and bound["state"]["process"] == process,
        "Exact observer binding mismatch",
    )


@contextmanager
def restoration_guard(owner):
    with owner.control.cleanup_guard():
        require(time.monotonic() < owner.anchor + 900, "Original restoration cutoff expired")
        require(
            owner.service is None or owner.service.child.poll() is not None,
            "Packaged service still live",
        )
        owner.require_stopped()
        require(
            not owner.profiles.journal.exists() and not owner.profiles.journal.is_symlink(),
            "Activation journal hold",
        )
        yield


def restore_catalogue(owner):
    owner.verify_cleanup_owner()
    owner.require_settled_input()
    require(owner.dispatch_settled, "Unsettled packaged dispatch; restoration refused")
    require(owner.baseline is not None, "Complete catalogue baseline required")
    p = owner.profiles
    target_id = owner.config["target"]["profile_id"]
    return_id = owner.job["return_profile"]
    with owner.app._locked(nonblocking=True, recover_removal=False), restoration_guard(owner):
        active = p._state()["active"]
        require(active in (target_id, return_id), "Foreign active profile; restoration refused")
        require(
            active == target_id or not owner.open_attempted,
            "Unexpected return activation after dispatch",
        )
        target = p.live if active == target_id else p._profile_path(target_id)
        returned = p._profile_path(return_id) if active == target_id else p.live
        require(tree_files(returned) == owner.baseline["return_files"], "Return profile changed")
        target_files_unchanged(owner, target)
        verify_sibling_saves(owner)
        p._marker(target, target_id)
        require(
            p._asset_hash(target) == owner.config["target"]["assets_sha256"],
            "Target assets changed",
        )
        for path, facts in owner.baseline["immutable"].items():
            require(file_facts(path) == facts, "Protected catalogue/input changed")
        for row in owner.preimages:
            require(
                sha(read_plain(row["copy"], maximum=262144)) == row["sha256"],
                "Declared preimage changed",
            )
        target_post = tree_files(target)
        if active == target_id:
            # The outer restoration guard already holds the same STATE lock.
            # Keep that fence through the maintained exchange without reentry.
            p.switch(return_id, mutation_guard=nullcontext)
        target = p._profile_path(target_id)
        for index, row in enumerate(owner.preimages):
            path = target / "Settings.ini" if row["role"] == "settings" else Path(row["path"])
            before = read_plain(row["copy"], maximum=262144)
            require(sha(before) == row["sha256"], "Declared preimage changed")
            post = read_plain(path, maximum=262144)
            post_info = file_facts(path)
            post_path = owner.directory / ("mutable-post-" + str(index))
            with post_path.open("xb") as stream:
                stream.write(post)
            temporary = path.with_name(path.name + ".menu-restore-" + uuid.uuid4().hex)
            try:
                with temporary.open("xb") as stream:
                    stream.write(before)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, row["mode"])
                os.utime(temporary, ns=(row["mtime_ns"], row["mtime_ns"]))
                require(file_facts(path) == post_info, "Mutable file changed during restoration")
                os.replace(temporary, path)
                _fsync_dir(path.parent)
            finally:
                if temporary.exists():
                    temporary.unlink()
            actual = file_facts(path)
            require(
                actual["sha256"] == row["sha256"]
                and actual["mode"] == row["mode"]
                and actual["mtime_ns"] == row["mtime_ns"],
                "Declared preimage readback failed",
            )
        require(
            tree_files(p.live) == owner.baseline["return_files"], "Return profile readback differs"
        )
        after = target_files_unchanged(owner, target)
        require(
            all(
                after.get(name) == facts
                for name, facts in target_post.items()
                if name != "Settings.ini"
            ),
            "Target postimages/new files lost",
        )
        for path, facts in owner.baseline["immutable"].items():
            require(file_facts(path) == facts, "Protected input readback differs")
        verify_sibling_saves(owner)
    owner.write(
        "restoration-readback.json",
        dict(
            restored=True,
            return_profile=return_id,
            target_profile=target_id,
            game_absent=True,
            journal_absent=True,
            all_new_files_retained=True,
            **(
                {"owned_autosave_preimage": str(owner.autosave_preimage)}
                if getattr(owner, "autosave_preimage", None)
                else {}
            ),
            elapsed=time.monotonic() - owner.anchor,
        ),
    )
    owner.journal.append(
        "restored", dict(restored=True, elapsed_seconds=time.monotonic() - owner.anchor)
    )
    return True


def pilot_class(common, agent_class):
    class CatalogueMenuPilot(agent_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.config = verify_config(self.job)
            self.service = None
            self.dispatch_settled = False
            self.preimages = []
            self.autosave_preimage = None

        def spawn_input(self, argv, *, deadline, before_spawn=None, **kwargs):
            def final_menu_guard():
                if (
                    getattr(self, "menu_work_active", False)
                    and time.monotonic() >= self.anchor + 390
                ):
                    raise MenuIncomplete("Menu work cutoff before input; normal quit next")
                if before_spawn is not None:
                    before_spawn()

            return super().spawn_input(
                argv, deadline=deadline, before_spawn=final_menu_guard, **kwargs
            )

        def prepare(self):
            from agent_actor_binding import collect_actor

            self.verify_available()
            actor = collect_actor(self.job["actor_binding"])
            require(
                (actor["model"], actor["effort"], actor["approval_policy"], actor["sandbox"])
                == ("gpt-6.1-sol", "high", "never", "danger-full-access"),
                "Actual driver provenance mismatch",
            )
            self.verify_cleanup_owner()
            self.verify_code()
            self.require_stopped()
            # Signed packages legitimately contain framework/resource symlinks.
            # Verify their maintained signature rather than treating the bundle
            # as a plain mutable profile tree.
            bundle = self.config["package"]["bundle"]
            subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", bundle],
                check=True,
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["/usr/sbin/spctl", "--assess", "--type", "execute", bundle],
                check=True,
                capture_output=True,
                timeout=10,
            )
            p = self.profiles
            require(
                not p.journal.exists() and not p.journal.is_symlink(), "Activation journal hold"
            )
            require(p._state()["active"] == self.job["return_profile"], "Current return changed")
            require(
                self.app.installation.executable_sha256 == self.job["game_sha256"],
                "Game build changed",
            )
            target = self.config["target"]
            records = [r for r in self.app.catalogue() if r.profile_id == target["profile_id"]]
            require(len(records) == 1, "Exact existing catalogue edition required")
            record = records[0]
            require(
                record.archive_sha256 == target["archive_sha256"]
                and record.variant_id == target["variant_id"]
                and list(record.scenarios) == target["scenarios"],
                "Catalogue target drift",
            )
            root = p._profile_path(record.profile_id)
            p._marker(root, record.profile_id)
            require(
                (root / "Logs").is_dir() and not (root / "Logs").is_symlink(),
                "Existing plain target Logs directory required",
            )
            require(p._asset_hash(root) == target["assets_sha256"], "Target asset drift")
            baseline = dict(
                return_files=tree_files(p.live), target_files=tree_files(root), immutable={}
            )
            baseline["sibling_saves"] = sibling_saves(
                self.app, target["profile_id"], self.job["return_profile"]
            )
            paths = [self.app.catalogue_file, self.app.originals / (record.archive_sha256 + ".7z")]
            if self.job["purpose"] == SAVE_PURPOSE:
                paths.append(Path(autosave_origin(self.job)["origin_readback"]))
            if self.config["prepare_compatibility"]:
                selection = self.config["selection"]
                selected = [
                    r for r in self.app.catalogue() if r.profile_id == selection["profile_id"]
                ]
                require(
                    len(selected) == 1
                    and self.app._is_original_variant(selected[0])
                    and selected[0].archive_sha256 == selection["archive_sha256"]
                    and selected[0].variant_id == selection["variant_id"]
                    and list(selected[0].scenarios) == selection["scenarios"],
                    "Current original selection drift",
                )
                original_root = p._profile_path(selection["profile_id"])
                p._marker(original_root, selection["profile_id"])
                for base in (original_root, self.app.prepared / selected[0].prepared_directory):
                    paths.extend(base / name for name in tree_files(base))
            for base in (
                self.app.imports / record.imported_directory,
                self.app.prepared / record.prepared_directory,
                p._profile_path("original-game"),
            ):
                paths.extend(base / name for name in tree_files(base))
            baseline["immutable"] = {str(path): file_facts(path) for path in paths}
            require(baseline == self.config["preservation"], "Fresh catalogue baseline drift")
            require(
                sha(read_plain(paths[1], maximum=2 * 1024**3)) == record.archive_sha256,
                "Archive drift",
            )
            self.baseline = baseline
            if self.job["purpose"] == SAVE_PURPOSE:
                self.autosave_preimage = preserve_owned_autosave(self, root)
                target_files_unchanged(self, root)
            for role, path in (
                ("settings", root / "Settings.ini"),
                ("feral", self.app.installation.support_root / "Preferences Data"),
                (
                    "global",
                    Path.home() / "Library/Preferences/com.feralinteractive.railroads.plist",
                ),
            ):
                data = read_plain(path, maximum=262144)
                facts = file_facts(path)
                require(
                    facts == self.config["mutable_preimages"][role], "Fresh mutable preimage drift"
                )
                copy = self.directory / (role + "-preimage")
                shutil.copy2(path, copy)
                require(read_plain(copy) == data, "Preimage readback failed")
                self.preimages.append(dict(role=role, path=str(path), copy=str(copy), **facts))
            self.write(
                "preflight.json",
                dict(
                    return_profile=self.job["return_profile"],
                    target=target,
                    baseline=baseline,
                    mutable_preimages=self.preimages,
                    **(
                        {"owned_autosave_preimage": str(self.autosave_preimage)}
                        if self.autosave_preimage
                        else {}
                    ),
                ),
            )
            self.phases["preparation_complete"] = time.monotonic()

        def launch(self):
            if self.job.get("purpose") == SAVE_PURPOSE:
                number = self.next_launch()
                with self.launch_files(number):
                    self.launches = number
                    self.service = None
                    self.dispatch_settled = False
                    self.helper_packet = self.directory / "observer"
                    self.helper_packet.mkdir(mode=0o700)
                    self.launch_deadline = self.anchor + (120 if number == 1 else 390)
                    self._launch_packaged()
            else:
                require(self.launches == 0, "One packaged launch maximum")
                self.launches = 1
                self._launch_packaged()

        def _launch_packaged(self):
            self.require_stopped()
            self.service = PackagedService(self)
            ready_deadline = (
                self.anchor + 60
                if self.launches == 1
                else min(self.anchor + 390, time.monotonic() + 10)
            )
            self.service.receive(event="ready", deadline=min(ready_deadline, self.launch_deadline))
            target = self.config["target"]
            prepare = self.config["prepare_compatibility"]
            selection = self.config["selection"] if prepare else target
            scope = dict(
                profile_id=selection["profile_id"],
                archive_sha256=selection["archive_sha256"],
                variant_id=selection["variant_id"],
                game_executable_sha256=self.job["game_sha256"],
                prepare_compatibility=prepare,
                launch_deadline=self.launch_deadline,
            )
            self.service.send("play-review", "maptools.play_preflight", scope)
            reply = self.service.receive(deadline=self.launch_deadline)
            require(reply.get("ok") is True, "Packaged preflight refused")
            review = reply["data"]
            expected = {
                k: scope[k]
                for k in ("profile_id", "archive_sha256", "variant_id", "game_executable_sha256")
            }
            require(
                review["target"] == expected
                and review["warning"] == self.config["warning"]
                and review["preparation"] == (self.config["preparation"] if prepare else None)
                and review["prepare_compatibility"] is prepare
                and review["launch_deadline"] == self.launch_deadline,
                "Packaged review scope drift",
            )
            self.observer_admission = arm_observer(self, common)
            self.request = time.monotonic()
            self.request_wall_us = time.time_ns() // 1000
            self.phases["launch_request"] = self.request
            self.publish(
                "request.json",
                dict(
                    version=1,
                    job=self.job["job"],
                    nonce=self.nonce,
                    code=self.code,
                    anchor=self.anchor,
                    request=self.request,
                ),
            )
            self.write(
                "packaged-request-interval.json",
                dict(
                    start=self.request,
                    wall_us=self.request_wall_us,
                    deadline=self.launch_deadline,
                    service=asdict(self.service.identity),
                    target=target,
                    one_commit=True,
                ),
            )
            self.service.send(
                "play-commit",
                "maptools.play_commit",
                dict(
                    scope,
                    confirmed=True,
                    ticket=review["ticket"],
                    fingerprint=review["fingerprint"],
                ),
                commit=True,
            )
            reply = self.service.receive(deadline=min(self.request + 25, self.launch_deadline))
            self.write("packaged-play-response.json", reply)
            data = reply.get("data", {})
            if reply.get("ok") is True and data.get("launch_dispatched") is False:
                self.dispatch_settled = True
                self.open_attempted = False
                raise ValueError("Packaged launch cancelled before dispatch")
            require(
                reply.get("ok") is True
                and data.get("target") == expected
                and data.get("launched_profile_id") == target["profile_id"],
                "Packaged Play outcome unknown",
            )
            bind_observer(self, self.observer_admission)
            self.dispatch_settled = True
            self.service.settle()
            self.write(
                "creation-ledger.json",
                dict(
                    processes=self.creation,
                    controller=self.controller_identity,
                    scope="Exact direct service, observer and game identities; no descendant closure claim",
                ),
            )
            engine = dict(
                game_sha256=self.job["game_sha256"],
                catalogue_target=target,
                selected_catalogue_edition=selection,
                prepare_compatibility=prepare,
            )
            selection_receipt = dict(
                process={k: asdict(self.game)[k] for k in ("pid", "birth_us", "executable")},
                selected_at_us=time.time_ns() // 1000,
                observed_inputs=self.job["source_original"],
                observed_engine_inputs=engine,
                observed_input_identity=sha(canonical(engine)),
            )
            if self.launches == 1:
                self.journal.append("selected", selection_receipt)
            else:
                self.write("relaunch-selection.json", selection_receipt)

        def guard_running(self, *, allow_exit=False):
            self.verify_available()
            self.admitted()
            self.verify_code()
            require(time.monotonic() < self.anchor + 480, "Fixed catalogue menu deadline expired")
            if getattr(self, "menu_work_active", False) and time.monotonic() >= self.anchor + 390:
                raise MenuIncomplete("Menu discovery cutoff reached; normal quit next")
            require(
                self.profiles._state()["active"] == self.config["target"]["profile_id"]
                and not self.profiles.journal.exists(),
                "Catalogue target ownership drift",
            )
            games = self.monitor.games()
            require(len(games) <= 1, "Ambiguous game process")
            if allow_exit and not games:
                require(
                    self.game is not None and self.monitor.inspect(self.game.pid) is None,
                    "Unresolved quit identity",
                )
                return
            require(
                len(games) == 1 and self.game is not None and self.game.same(games[0]),
                "Owned catalogue game identity drift",
            )
            require(self.observer.poll() is None, "Independent observer exited")
            self.sample()

        def observe(self):
            if self.job["purpose"] == SAVE_PURPOSE:
                return self.observe_save_reopen()
            observations = {}
            partial = None
            loading = self.job["purpose"] == LOAD_PURPOSE
            instructions = (
                (
                    "launcher",
                    "Wait for the Feral Play control. Activate only its fresh unique AX Play once; observe the game main menu.",
                ),
                (
                    "menu",
                    "From the current game main menu open visible Credits or Options once. No Single Player, Load, scenario, settings changes or save actions. Observe that reversible submenu.",
                ),
                (
                    "returned",
                    "Return once using visible Back or Escape; observe the main menu. No gameplay, settings changes, loading or saves.",
                ),
            )
            if loading:
                instructions = (
                    instructions[0],
                    (
                        "world",
                        "Use visible Single Player and verify the selected scenario title is exactly "
                        + repr(self.config["scenario_title"])
                        + " before starting once. Observe its playable world, HUD funds/year and actual city; pause using the visible control. No construction, purchases, Load, saves or settings changes. If the exact scenario cannot be verified or loading cannot finish promptly, return partial with no input; normal quit follows.",
                    ),
                )
            self.menu_work_active = True
            try:
                for stage, instruction in instructions:
                    if time.monotonic() >= self.anchor + 390:
                        raise MenuIncomplete("Menu discovery cutoff reached")
                    observations[stage] = self.decisions.stage(stage, instruction)
            except MenuIncomplete as exc:
                partial = str(exc)
            finally:
                self.menu_work_active = False
            self.normal_quit("finished", None)
            return dict(
                status="safety_stop",
                cause=(
                    "packaged_cached_scenario_load_requires_review"
                    if loading
                    else "packaged_catalogue_menu_requires_review"
                ),
                world_loaded="agent_observed" if "world" in observations else "unknown",
                gameplay_credit=False,
                save_reopen_credit=False,
                evidence_kind="agent_visual_observations",
                human_confirmation=False,
                partial_reason=partial,
                **observations,
            )

        def observe_save_reopen(self):
            from save_cycle import observe_save_cycle
            from unique_save_file import SaveIntent, SaveScope

            source = self.job["source_original"]
            self.scope = SaveScope(
                self.job["job"],
                source["archive_sha256"],
                source["variant_id"],
                source["scenario"],
                self.job["game_sha256"],
                self.config["target"]["profile_id"],
                str(self.profiles.live),
            )
            self.menu_work_active = True
            try:
                world = self.decisions.stage(
                    "world",
                    "Use visible Single Player and verify selected scenario title exactly "
                    + repr(self.config["scenario_title"])
                    + " before one Start. Observe playable world/HUD and pause with visible control. No construction, purchases or transport. Once the paused world is observed, you may pair that observation with CmdS once to open Save As. If the exact title/world is unavailable, return partial with no input.",
                )
                mapping = self.decisions.stage(
                    "filename",
                    "Open Save As if not already open. Enter exactly "
                    + self.job["agent_save"]["token"]
                    + "; observe the filename field/caret and do not save yet. Refuse overwrite. Return partial with no input if the field cannot be identified.",
                )
                intent = SaveIntent(
                    str(self.profiles.live / "Saves"),
                    self.job["agent_save"]["filename"],
                    sha(canonical(mapping)),
                )
                outcome = observe_save_cycle(
                    self,
                    self.scope,
                    intent,
                    minimum_stable_seconds=1,
                    deadline=self.anchor + 480,
                    scratch_autosaves=tuple(f"Auto{i}.SMRailroadsSavedGame" for i in range(5)),
                )
                outcome.update(
                    cause="packaged_cached_save_exact_reopen_requires_review",
                    world_loaded="agent_observed",
                    world=world,
                    evidence_kind="agent_visual_observations",
                    human_confirmation=False,
                )
                return outcome
            except MenuIncomplete as exc:
                self.menu_work_active = False
                self.normal_quit("finished", None)
                return dict(
                    status="safety_stop",
                    cause="packaged_cached_save_prerequisite_incomplete",
                    partial_reason=str(exc),
                    world_loaded="unknown",
                    gameplay_credit=False,
                    save_reopen_credit=False,
                    evidence_kind="agent_visual_observations",
                    human_confirmation=False,
                )
            finally:
                self.menu_work_active = False

        def normal_quit(self, stage, validate):
            work = getattr(self, "menu_work_active", False)
            if self.job.get("purpose") == SAVE_PURPOSE:
                self.menu_work_active = False
            try:
                return super().normal_quit(stage, validate)
            finally:
                self.menu_work_active = work

        def observe_reopened_world_and_continued_action(self, *, validate_delivery, deadline):
            if self.job["purpose"] != SAVE_PURPOSE:
                return super().observe_reopened_world_and_continued_action(
                    validate_delivery=validate_delivery, deadline=deadline
                )
            require(deadline == self.anchor + 480, "Original cycle deadline required")
            validate_delivery()
            self.normal_quit("finished", validate_delivery)
            return dict(scope="exact_reopened_world_only", continued_action=False)

        def stop(self):
            self.verify_cleanup_owner()
            if self.service is not None and not self.service.stdout.closed:
                self.service.settle()
            if self.open_attempted and self.game is None:
                # EOF/death fences future service dispatch, but never cancels an
                # already asynchronous unknown LaunchServices request.
                raise ValueError("Packaged launch unbound; observer retained, restoration held")
            if self.service is not None and not self.service.commit_sent:
                self.dispatch_settled = True
            if self.service is None:
                self.dispatch_settled = True
            return super().stop()

        def restore(self):
            return restore_catalogue(self)

    return CatalogueMenuPilot

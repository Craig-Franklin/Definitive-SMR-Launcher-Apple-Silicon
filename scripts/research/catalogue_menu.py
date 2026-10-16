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
    require(job["purpose"] in (PURPOSE, CACHED_PURPOSE), "Closed catalogue menu purpose required")
    cached = job["purpose"] == CACHED_PURPOSE
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
        existing_target_unchanged(target, owner.baseline["target_files"])
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
        after = existing_target_unchanged(target, owner.baseline["target_files"])
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
                ),
            )
            self.phases["preparation_complete"] = time.monotonic()

        def launch(self):
            require(self.launches == 0, "One packaged launch maximum")
            self.launches = 1
            self.require_stopped()
            self.service = PackagedService(self)
            self.service.receive(
                event="ready", deadline=min(self.anchor + 60, self.launch_deadline)
            )
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
            self.journal.append(
                "selected",
                dict(
                    process={k: asdict(self.game)[k] for k in ("pid", "birth_us", "executable")},
                    selected_at_us=time.time_ns() // 1000,
                    observed_inputs=self.job["source_original"],
                    observed_engine_inputs=engine,
                    observed_input_identity=sha(canonical(engine)),
                ),
            )

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
            observations = {}
            partial = None
            self.menu_work_active = True
            try:
                for stage, instruction in (
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
                ):
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
                cause="packaged_catalogue_menu_requires_review",
                world_loaded="unknown",
                gameplay_credit=False,
                save_reopen_credit=False,
                evidence_kind="agent_visual_observations",
                human_confirmation=False,
                partial_reason=partial,
                **observations,
            )

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

"""Finite-job availability control from native Codex direct user records.

Admission supplies an exact owner/job and a complete rollout prefix. Only new
single-text native user messages containing one standalone command are accepted.
All writers of the admitted STATE file must use ``state_lock``; atomic replacement
and a final byte comparison refuse observed foreign writes. This is a cooperative
local-file protocol, not an access-control boundary against a hostile writer.
No transcript body is retained in state, receipts, exceptions or logs.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid


# Native history grows across the same long-lived Run; other control files keep
# their smaller default bound. Identity, prefix and authoritative-row limits stay strict.
MAX_NATIVE_HISTORY_BYTES = 512 * 1024 * 1024


class ControlRefused(RuntimeError):
    pass


class UserCancelled(ControlRefused):
    pass


def require(condition, reason):
    if not condition:
        raise ControlRefused(reason)


def decode(data):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "Duplicate control record key")
            result[key] = value
        return result

    try:
        return json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise ControlRefused("Malformed control record") from exc


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_plain(path, maximum=64 * 1024 * 1024, *, identity=None):
    path = Path(path)
    require(
        path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
        "Plain absolute control path required",
    )
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= maximum,
            "Bounded regular control file required",
        )
        if identity is not None:
            require(
                identity == {"device": before.st_dev, "inode": before.st_ino},
                "Admitted native stream identity changed",
            )
        data = stream.read(before.st_size)
        after = os.fstat(stream.fileno())
        current = path.stat(follow_symlinks=False)
    require(
        len(data) == before.st_size
        and (before.st_dev, before.st_ino)
        == (after.st_dev, after.st_ino)
        == (current.st_dev, current.st_ino)
        and after.st_size >= before.st_size,
        "Control file truncated during read",
    )
    return data


@contextmanager
def state_lock(path):
    """All admission, availability and owner writers share this stable lock."""
    path = Path(path)
    require(
        path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
        "Plain state path required",
    )
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Plain state lock required")
        # Never wait indefinitely behind an unavailable writer.
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControlRefused("State writer lock unavailable") from exc
        yield
    finally:
        os.close(fd)


def replace_state(path, before, value):
    """Call only under state_lock; retain permissions and fsync both levels."""
    path = Path(path)
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd, name = tempfile.mkstemp(prefix=path.name + ".control-", dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IMODE(path.stat().st_mode))
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        require(read_plain(path) == before, "State changed outside writer lock")
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        require(read_plain(path) == data, "Durable state readback mismatch")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def timestamp(value):
    try:
        require(type(value) is str, "Availability timestamp required")
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.tzinfo is not None, "Availability timezone required")
        return result
    except ValueError as exc:
        raise ControlRefused("Malformed availability timestamp") from exc


def direct_user_command(row, choices):
    """Match a caller's closed command vocabulary in a real direct user record.

    Return only the chosen label, native timestamp and message ID. Quoted text,
    attachments, tool replies and partial/embedded commands are not authority.
    This does not interpret observations or authenticate game state.
    """
    if type(row) is not dict or row.get("type") != "response_item":
        return None
    item = row.get("payload")
    if type(item) is not dict or item.get("type") != "message" or item.get("role") != "user":
        return None
    meta = item.get("internal_chat_message_metadata_passthrough")
    content = item.get("content")
    if (
        type(meta) is not dict
        or meta.get("content_item_kinds") != ["user.text"]
        or type(meta.get("turn_id")) is not str
        or not meta["turn_id"]
        or type(meta.get("create_time")) not in (int, float)
        or not math.isfinite(meta["create_time"])
        or type(item.get("id")) is not str
        or not item["id"].startswith("msg_")
        or type(content) is not list
        or len(content) != 1
        or type(content[0]) is not dict
        or content[0].get("type") != "input_text"
        or type(content[0].get("text")) is not str
    ):
        return None
    # Deliberately bounded command surface. No prose, quoting, Markdown, multiple
    # commands, examples, attachments, or semantic/negation inference.
    text = content[0]["text"].strip().replace("’", "'").casefold()
    if text.endswith((".", "!")):
        text = text[:-1]
    if text not in choices:
        return None
    when = timestamp(row.get("timestamp"))
    require(abs(when.timestamp() - meta["create_time"]) < 1, "Native message clock mismatch")
    return choices[text], when, item["id"]


def command(row):
    """Keep availability's existing whole-message command surface unchanged."""
    return direct_user_command(
        row,
        {
            "i'm playing": "PLAYING",
            "i want to play": "PLAYING",
            "i'm done playing": "AVAILABLE",
            "you can use the game": "AVAILABLE",
        },
    )


def binding_for(job):
    root, control = job["root"], job["active_control"]
    minimum_generation = 9
    job_prefix = "finite-uk-calibration"
    if "goal_scope" in job:
        from goal_owner import verify_root_scope

        minimum_generation = verify_root_scope(job, job["actor_binding"]["driver_actor"])
        job_prefix = "finite-goal"
    require(
        set(root) == {"run", "consumer_generation", "session", "coordinator"}
        and type(root["consumer_generation"]) is int
        and root["consumer_generation"] >= minimum_generation
        and type(root["run"]) is str
        and re.fullmatch(r"run_[0-9a-f]{12}", root["run"])
        and type(root["coordinator"]) is str
        and root["coordinator"].startswith("term_")
        and str(uuid.UUID(root["session"])) == root["session"],
        "Exact current job owner required",
    )
    require(
        set(control) == {"nonce", "start_offset", "prefix_sha256", "not_before", "stream_identity"}
        and str(uuid.UUID(control["nonce"])) == control["nonce"]
        and type(control["start_offset"]) is int
        and control["start_offset"] > 0
        and type(control["prefix_sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", control["prefix_sha256"]),
        "Fresh reviewed native-message boundary required",
    )
    identity = control["stream_identity"]
    require(
        type(identity) is dict
        and set(identity) == {"device", "inode"}
        and type(identity["device"]) is int
        and identity["device"] >= 0
        and type(identity["inode"]) is int
        and identity["inode"] > 0,
        "Exact admitted native stream device/inode required",
    )
    timestamp(control["not_before"])
    require(
        job["job"]
        == f"{job_prefix}-{root['session']}-generation{root['consumer_generation']}-{control['nonce']}"
        and job["actor_binding"]["root"] == root
        and job["actor_binding"]["root_actor"]["session"] == root["session"]
        and job["actor_binding"]["root_actor"]["terminal"] == root["coordinator"],
        "Fresh exact job/actor binding required; historical jobs cannot be substituted",
    )
    return dict(
        job=job["job"], root=root, rollout=job["actor_binding"]["root_actor"]["rollout"], **control
    )


def verify_run_owner(job):
    """Read supported native Run ownership; STATE equality is a separate fence.

    This bounded check is not atomic with later API operations or kernel signals.
    The STATE lock excludes only participating local STATE writers.
    """
    try:
        raw = subprocess.check_output(
            [job["orca"], "orchestration", "run-show", "--id", job["root"]["run"], "--json"],
            timeout=3,
        )
        require(len(raw) <= 65536, "Native Run receipt size bound")
        receipt = decode(raw)
        run = receipt["result"]["run"]
        require(
            receipt.get("ok") is True
            and type(run["consumer_generation"]) is int
            and run["consumer_generation"] == job["root"]["consumer_generation"]
            and run["coordinator_handle"] == job["root"]["coordinator"],
            "Current native Run owner changed",
        )
        runtime = job["actor_binding"].get("provider_runtime_id")
        if runtime is not None:
            require(
                receipt.get("_meta", {}).get("runtimeId") == runtime,
                "Native Run service runtime changed",
            )
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as exc:
        raise ControlRefused("Current native Run ownership unavailable") from exc


class ActiveJobControl:
    def __init__(self, job):
        self.job = job
        self.binding = binding_for(job)
        self.path = Path(job["availability_path"])
        self.cancelled = False
        self.last_cursor = self.binding["start_offset"]
        self.last_prefix = self.binding["prefix_sha256"]

    def validate_owner(self, state):
        require(type(state) is dict, "State object required")
        owner = state.get("effective_continuation")
        expected = dict(
            self.binding["root"],
            status="ACTIVE_ONE_FINITE_CALIBRATION_WAVE",
            model="gpt-6.1-sol",
            effort="xhigh",
            approval_policy="never",
            sandbox="danger-full-access",
            runtime_automation_allowed=True,
            runtime_admitted=True,
            admitted_job=self.binding["job"],
            exclusive_runtime_owner=self.binding["root"]["coordinator"],
            closed_circuits_unchanged=True,
            manual_collector_allowed=False,
            capacity_recovery_enabled=False,
        )
        require(
            type(owner) is dict
            and all(
                type(owner.get(k)) is type(v) and owner.get(k) == v for k, v in expected.items()
            ),
            "Current owner-specific live admission changed",
        )
        active = state.get("active_job_control")
        require(
            type(active) is dict
            and active.get("binding") == self.binding
            and active.get("phase") == "active"
            and type(active.get("cancelled")) is bool
            and (not self.cancelled or active["cancelled"])
            and type(active.get("cursor")) is int
            and active["cursor"] >= self.last_cursor
            and type(active.get("prefix_sha256")) is str,
            "Active job replaced, malformed or cancellation latch lost",
        )
        return active

    def validate(self, state):
        active = self.validate_owner(state)
        availability = state.get("game_availability")
        require(
            type(availability) is dict and availability.get("status") in ("AVAILABLE", "PLAYING"),
            "Unknown game availability",
        )
        timestamp(availability.get("updated_at"))
        return active, availability

    @contextmanager
    def cleanup_guard(self):
        """Fence cooperative owner replacement across one cleanup mutation.

        Availability does not revoke exact-owned cleanup. This guard holds the
        shared writer lock through signals/profile exchange, including the
        backend's TERM-to-KILL wait, so a new owner cannot be installed midway.
        It must not contain a nested poll or any new game/provider input.
        """
        with state_lock(self.path):
            self.validate_owner(decode(read_plain(self.path)))
            verify_run_owner(self.job)
            yield

    def verify_owner(self):
        """Read-only emergency cleanup fence; no native parsing or state writes."""
        with self.cleanup_guard():
            pass

    @contextmanager
    def mutation_guard(self):
        """Check and fence one immediate mutation, never a blocking child wait.

        Callers acquire any launcher lock nonblocking before this STATE lock.
        Do not nest public polls in this context. Native Run checks and native
        appends are separate operations; no cross-API atomicity is claimed.
        """
        with state_lock(self.path):
            self._poll_locked()
            if self.cancelled:
                raise UserCancelled(
                    "User requested game handback; active job permanently cancelled"
                )
            require(
                decode(read_plain(self.path)).get("paused") is False,
                "Project pause forbids new runtime mutations",
            )
            yield

    @contextmanager
    def finalization_guard(self):
        """Drain late commands and fence final journal/index writes together.

        This fallible drain is never used for the emergency safety stop.
        """
        with state_lock(self.path):
            self._poll_locked()
            yield

    def _poll_locked(self):
        """Consume complete records under the caller's single STATE lock."""
        before = read_plain(self.path)
        state = decode(before)
        active, availability = self.validate(state)
        verify_run_owner(self.job)
        data = read_plain(
            self.binding["rollout"],
            maximum=MAX_NATIVE_HISTORY_BYTES,
            identity=self.binding["stream_identity"],
        )
        start = self.binding["start_offset"]
        cursor = active["cursor"]
        require(
            start <= cursor <= len(data)
            and data[start - 1 : start] == b"\n"
            and data[cursor - 1 : cursor] == b"\n"
            and sha(data[:start]) == self.binding["prefix_sha256"]
            and sha(data[:cursor]) == active["prefix_sha256"]
            and sha(data[: self.last_cursor]) == self.last_prefix,
            "Native rollout prefix or cursor changed",
        )
        first = decode(data.split(b"\n", 1)[0])
        require(
            first.get("type") == "session_meta"
            and first.get("payload", {}).get("id") == self.binding["root"]["session"]
            and first["payload"].get("originator") == "codex-tui"
            and first["payload"].get("source") == "cli",
            "Native coordinator session mismatch",
        )
        active["cancelled"] |= availability["status"] == "PLAYING"
        end = data.rfind(b"\n") + 1
        for line in data[cursor:end].splitlines():
            # The complete stream is already bounded by read_plain. Native
            # image/tool records can exceed 1 MiB without carrying authority.
            require(len(line) > 0, "Empty native record")
            parsed = command(decode(line))
            require(parsed is None or len(line) <= 1024 * 1024, "Native control record size bound")
            cursor += len(line) + 1
            if parsed is None:
                continue
            status, when, message_id = parsed
            # Native history replay may append old user records again. A
            # new byte offset alone is not proof of a new instruction.
            if when <= timestamp(self.binding["not_before"]):
                continue
            active["cancelled"] |= status == "PLAYING"
            if when > timestamp(availability["updated_at"]):
                availability = dict(
                    status=status,
                    updated_at=when.astimezone(timezone.utc).isoformat(),
                    source="native_direct_user_command",
                    session=self.binding["root"]["session"],
                    message_id=message_id,
                )
                state["game_availability"] = availability
        active.update(cursor=cursor, prefix_sha256=sha(data[:cursor]))
        if decode(before) != state:
            replace_state(self.path, before, state)
        self.cancelled = active["cancelled"]
        self.last_cursor, self.last_prefix = cursor, active["prefix_sha256"]

    def poll(self, *, cancel=True):
        """Persist user commands before raising; cleanup may poll without raising."""
        with state_lock(self.path):
            self._poll_locked()
        if cancel and self.cancelled:
            raise UserCancelled("User requested game handback; active job permanently cancelled")


def main():
    """Coordinator can drain real native commands through the same locked path.

    This command accepts no status override, admission or job creation. It only
    drains an already admitted exact job's new native records. The active parent
    performs this same poll autonomously while its bounded children run.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--job-sha256", required=True)
    args = parser.parse_args()
    raw = read_plain(args.job)
    require(sha(raw) == args.job_sha256, "Explicit exact job pin mismatch")
    job = decode(raw)
    require(
        os.environ.get("CODEX_THREAD_ID") == job["root"]["session"]
        and os.environ.get("ORCA_TERMINAL_HANDLE") == job["root"]["coordinator"],
        "Actual bound coordinator caller required",
    )
    active = ActiveJobControl(job)
    active.poll(cancel=False)
    print(json.dumps(dict(cancelled=active.cancelled, cursor=active.last_cursor), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ControlRefused, OSError, KeyError, TypeError, ValueError):
        # A JSON decode/path failure can carry private bytes in its exception.
        # Emit only a closed operational refusal, never the exception payload.
        print(
            "Active job control refused; identity/state/native record validation failed.",
            file=sys.stderr,
        )
        raise SystemExit(2)

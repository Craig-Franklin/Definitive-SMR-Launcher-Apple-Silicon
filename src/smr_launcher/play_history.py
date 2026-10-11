"""Private append-only play facts. No game control, raw diagnostics or publication.

An open is a confirmed process incarnation; a game can span several opens.
GitHub snapshots and fix tests are independent of completion and verification.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import uuid

from .activation import _assert_no_symlink_ancestor, _fsync_dir
from .scenario_evidence import canonical, decode, open_plain

REPOSITORY = "Craig-Franklin/Definitive-SMR-Launcher-Apple-Silicon"
ISSUE_PREFIX = "https://github.com/" + REPOSITORY + "/issues/"
MAX_BYTES, MAX_EVENTS = 16 * 1024 * 1024, 10000
IDENTITIES = ("archive_sha256", "variant_id", "game_executable_sha256")
LABELS = dict(
    launch_attempted="Launch attempted",
    process_confirmed="Process confirmed",
    game_started="New game started",
    game_resumed="Existing game resumed",
    map_loaded="Map loaded",
    playable="Playable",
    save_made="Save made",
    save_reopened="Exact save reopened",
    continued_play="Continued play",
    retired="Retired",
    goals_met="All scenario goals met",
    quit="Quit confirmed",
    crash_load="Crashed during load",
    crash_play="Crashed during play",
    crash_unknown="Crash · phase unknown",
    resource_stop="Resource stop",
    cancelled="Cancelled",
    unknown_exit="Outcome unknown",
    issue_link="Issue linked",
    issue_state="Issue status checked",
    fix_proposed="Fix proposed",
    fix_implemented="Fix implemented",
    fix_test_passed="Fix test passed",
    fix_test_failed="Fix test failed",
    prompt_offered="Checkpoint prompt offered",
)
CHECKPOINTS = (
    "map_loaded",
    "playable",
    "save_made",
    "save_reopened",
    "continued_play",
    "retired",
    "goals_met",
    "quit",
    "crash_load",
    "crash_play",
    "crash_unknown",
    "resource_stop",
    "cancelled",
)
SUCCESS = (
    "map_loaded",
    "playable",
    "save_made",
    "save_reopened",
    "continued_play",
    "retired",
    "goals_met",
)
FAILURES = ("crash_load", "crash_play", "crash_unknown", "resource_stop")
FIELDS = {
    "schema",
    "event_id",
    "at",
    "kind",
    "source",
    *IDENTITIES,
    "scenario",
    "attempt_id",
    "game_id",
    "save_id",
    "incident_id",
    "issue_url",
    "issue_state",
    "fix_id",
    "target_id",
    "checkpoint",
    "scope",
}


class HistoryError(ValueError):
    """History needs inspection; callers must not infer green or zero counts."""


def digest(*parts):
    return hashlib.sha256(canonical(parts)).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def instant(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError()
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise HistoryError("History timestamp needs an explicit time zone") from None


def issue_number(url):
    if not isinstance(url, str) or not re.fullmatch(
        re.escape(ISSUE_PREFIX) + r"[1-9][0-9]{0,9}", url
    ):
        raise HistoryError("Use an exact issue URL in the personal launcher repository")
    return int(url.rsplit("/", 1)[1])


def context(record, game_sha256, scenario=""):
    return dict(
        archive_sha256=record.archive_sha256,
        variant_id=record.variant_id,
        game_executable_sha256=game_sha256,
        scenario=scenario,
    )


def same_map(a, b):
    return all(a[k] == b[k] for k in IDENTITIES)


def same_scope(a, b):
    return same_map(a, b) and a["scenario"] == b["scenario"]


def validate(e):
    if (
        not isinstance(e, dict)
        or set(e) != FIELDS
        or type(e["schema"]) is not int
        or e["schema"] != 1
    ):
        raise HistoryError("Invalid history schema")
    for key in (
        *IDENTITIES,
        "event_id",
        "attempt_id",
        "game_id",
        "save_id",
        "incident_id",
        "fix_id",
        "target_id",
    ):
        if (
            not isinstance(e[key], str)
            or (not e[key] and key in (*IDENTITIES, "event_id"))
            or (e[key] and not re.fullmatch("[0-9a-f]{64}", e[key]))
        ):
            raise HistoryError("History requires exact bounded identities")
    scenario = e["scenario"]
    if not isinstance(scenario, str) or len(scenario) > 500:
        raise HistoryError("Invalid history scenario")
    if scenario:
        p = PurePosixPath(scenario)
        if (
            p.is_absolute()
            or str(p) != scenario
            or any(x in (".", "..") for x in p.parts)
            or "\\" in scenario
            or any(ord(c) < 32 for c in scenario)
        ):
            raise HistoryError("Scenario must be a plain relative path")
    instant(e["at"])
    if any(
        not isinstance(e[k], str)
        for k in ("kind", "source", "issue_url", "issue_state", "checkpoint", "scope")
    ):
        raise HistoryError("Invalid history text field")
    if e["kind"] not in LABELS or e["source"] not in ("observed", "player_confirmed", "historical"):
        raise HistoryError("Invalid history fact")
    if e["issue_url"]:
        issue_number(e["issue_url"])
    if e["scope"] not in ("exact", "related") or e["issue_state"] not in (
        "",
        "open",
        "closed",
        "unavailable",
    ):
        raise HistoryError("Invalid issue scope or snapshot")
    if e["checkpoint"] and e["checkpoint"] not in SUCCESS:
        raise HistoryError("Invalid failed checkpoint")
    if e["kind"] in ("issue_link", "issue_state") and not e["issue_url"]:
        raise HistoryError("Issue URL required")
    if e["kind"] == "issue_state" and not e["issue_state"]:
        raise HistoryError("Issue snapshot required")
    if (
        e["kind"]
        in CHECKPOINTS
        + ("process_confirmed", "game_started", "game_resumed", "unknown_exit", "prompt_offered")
        and not e["attempt_id"]
    ):
        raise HistoryError("An exact open is required")
    if e["kind"] in ("game_started", "game_resumed", "retired", "goals_met") and (
        not e["game_id"] or not scenario
    ):
        raise HistoryError("An exact game and scenario are required")
    if e["kind"] in ("save_reopened", "continued_play") and not e["save_id"]:
        raise HistoryError("Exact save identity required")
    if e["kind"] in FAILURES and not e["incident_id"]:
        raise HistoryError("Failure identity required")
    if e["kind"].startswith("fix_") and (not scenario or not e["fix_id"]):
        raise HistoryError("Fixes require an exact scenario and fix identity")
    if e["kind"] in ("fix_test_passed", "fix_test_failed") and (
        not e["target_id"] or not e["checkpoint"] or not e["attempt_id"]
    ):
        raise HistoryError("Fix test requires the failure, checkpoint and observed open")


def validate_relation(e, previous):
    """Reject invented games, cross-scope tests and unbound completion."""
    kind = e["kind"]
    needs_open = kind in CHECKPOINTS + (
        "game_started",
        "game_resumed",
        "fix_test_passed",
        "fix_test_failed",
    )
    if isinstance(previous, RelationIndex):
        confirmed = previous.confirmed.get((tuple(e[k] for k in IDENTITIES), e["attempt_id"]))
        attempted = (tuple(e[k] for k in IDENTITIES), e["attempt_id"]) in previous.attempted
    else:
        confirmed = next(
            (
                r
                for r in previous
                if same_map(e, r)
                and r["kind"] == "process_confirmed"
                and r["attempt_id"] == e["attempt_id"]
            ),
            None,
        )
        attempted = any(
            same_map(e, r)
            and r["kind"] == "launch_attempted"
            and r["attempt_id"] == e["attempt_id"]
            for r in previous
        )
    if needs_open:
        if not confirmed:
            # Imported failure facts can be valid without a reconstructable open.
            if not (
                (kind in FAILURES and e["source"] in ("observed", "historical"))
                or (kind == "cancelled" and attempted)
            ):
                raise HistoryError("Checkpoint has no confirmed process")
        if confirmed and instant(e["at"]) < instant(confirmed["at"]):
            raise HistoryError("Checkpoint predates its confirmed process")
    if kind.startswith("fix_"):
        rows = (
            previous.maps.get(tuple(e[k] for k in IDENTITIES), [])
            if isinstance(previous, RelationIndex)
            else previous
        )
        versions = [r for r in rows if r["fix_id"] == e["fix_id"]]
        if any(not same_scope(e, r) for r in versions):
            raise HistoryError("Fix identity belongs to another scenario")
        if kind in ("fix_proposed", "fix_implemented") and any(
            r["kind"] == "fix_implemented" for r in versions
        ):
            raise HistoryError(
                "This fix is already implemented; use a new fix identity for a changed fix"
            )
    if kind.startswith("fix_") and e["issue_url"]:
        rows = (
            previous.maps.get(tuple(e[k] for k in IDENTITIES), [])
            if isinstance(previous, RelationIndex)
            else previous
        )
        if not any(
            r["kind"] == "issue_link"
            and same_scope(e, r)
            and r["scope"] == "exact"
            and r["issue_url"] == e["issue_url"]
            for r in rows
        ):
            raise HistoryError("Fix linkage requires an exact-scope linked issue")
    if kind not in (
        "game_started",
        "game_resumed",
        "retired",
        "goals_met",
        "continued_play",
        "save_reopened",
        "fix_test_passed",
        "fix_test_failed",
    ):
        return
    rows = (
        previous.maps.get(tuple(e[k] for k in IDENTITIES), [])
        if isinstance(previous, RelationIndex)
        else [r for r in previous if same_map(e, r)]
    )
    if kind == "game_resumed":
        if not any(
            r["kind"] == "game_started" and r["game_id"] == e["game_id"] and same_scope(e, r)
            for r in rows
        ):
            raise HistoryError("Choose a known game from this exact edition/build/scenario")
    if kind == "game_started" and any(
        r["kind"] == kind and r["game_id"] == e["game_id"] for r in rows
    ):
        raise HistoryError("Game identity already started")
    if kind in ("retired", "goals_met"):
        if not any(
            r["kind"] in ("game_started", "game_resumed")
            and r["game_id"] == e["game_id"]
            and r["attempt_id"] == e["attempt_id"]
            and same_scope(e, r)
            for r in rows
        ):
            raise HistoryError("Bind this open to a new or resumed game before completion")
    if kind == "continued_play" and not any(
        r["kind"] == "save_reopened"
        and r["save_id"] == e["save_id"]
        and r["attempt_id"] == e["attempt_id"]
        and same_scope(e, r)
        for r in rows
    ):
        raise HistoryError("Confirm this exact save reopened before continued play")
    if kind == "save_reopened" and not any(
        r["kind"] == "save_made" and r["save_id"] == e["save_id"] and same_scope(e, r) for r in rows
    ):
        raise HistoryError("Record this exact save before confirming it reopened")
    if kind in ("fix_test_passed", "fix_test_failed"):
        target = next(
            (r for r in rows if r["event_id"] == e["target_id"] and r["kind"] in FAILURES), None
        )
        implemented = [
            r
            for r in rows
            if r["kind"] == "fix_implemented" and r["fix_id"] == e["fix_id"] and same_scope(e, r)
        ]
        if not target or not same_scope(e, target) or not target["scenario"] or not implemented:
            raise HistoryError(
                "Test must bind an implemented fix and an attributed failure in this exact scope"
            )
        if target["checkpoint"] != e["checkpoint"]:
            raise HistoryError("Test must pass the explicitly recorded failed checkpoint")
        boundary = max(instant(target["at"]), max(instant(r["at"]) for r in implemented))
        if instant(e["at"]) < boundary:
            raise HistoryError("Test predates the failure or implementation")
        if instant(confirmed["at"]) < boundary:
            raise HistoryError(
                "Use a newly confirmed open after the failure and fix implementation"
            )
        if kind == "fix_test_passed" and not any(
            r["kind"] == e["checkpoint"]
            and r["attempt_id"] == e["attempt_id"]
            and same_scope(e, r)
            and boundary <= instant(r["at"]) <= instant(e["at"])
            for r in rows
        ):
            raise HistoryError("Record the tested checkpoint after fix implementation")


class RelationIndex:
    """Constant-time process lookup while validating a bounded ledger."""

    def __init__(self, rows=()):
        self.maps, self.confirmed, self.attempted = {}, {}, set()
        for e in rows:
            self.add(e)

    def add(self, e):
        key = tuple(e[k] for k in IDENTITIES)
        self.maps.setdefault(key, []).append(e)
        if e["kind"] == "process_confirmed":
            self.confirmed[(key, e["attempt_id"])] = e
        elif e["kind"] == "launch_attempted":
            self.attempted.add((key, e["attempt_id"]))


class PlayHistoryStore:
    def __init__(self, library):
        self.root = Path(library) / "play-history"
        self.path = self.root / "events.jsonl"

    @contextmanager
    def _file(self, write=False):
        _assert_no_symlink_ancestor(self.root)
        if write:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.root.exists():
            yield None
            return
        info = self.root.stat()
        if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
            raise HistoryError("History directory must be private")
        if not write and not self.path.exists():
            _assert_no_symlink_ancestor(self.path)
            yield None
            return
        fd = open_plain(self.path, os.O_RDWR | os.O_CREAT if write else os.O_RDONLY, 0o600)
        try:
            info = os.fstat(fd)
            if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise HistoryError("History file must be private")
            fcntl.flock(fd, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            yield fd
        finally:
            os.close(fd)

    def _read(self, fd):
        if fd is None:
            return ()
        size = os.fstat(fd).st_size
        if size > MAX_BYTES:
            raise HistoryError("History size limit reached")
        os.lseek(fd, 0, os.SEEK_SET)
        data = bytearray()
        while len(data) < size:
            piece = os.read(fd, min(65536, size - len(data)))
            if not piece:
                raise HistoryError("Incomplete history read")
            data.extend(piece)
        if data and not data.endswith(b"\n"):
            raise HistoryError("Interrupted history append needs inspection")
        result, seen, relations = [], {}, RelationIndex()
        try:
            for line in data.splitlines():
                if len(result) >= MAX_EVENTS or len(line) > 8192:
                    raise HistoryError("History count/record limit reached")
                e = decode(line)
                validate(e)
                if e["event_id"] in seen:
                    raise HistoryError("Duplicate on-disk history identity")
                validate_relation(e, relations)
                relations.add(e)
                seen[e["event_id"]] = e
                result.append(e)
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise HistoryError("Local play history needs inspection") from exc
        return tuple(result)

    def records(self):
        with self._file() as fd:
            return self._read(fd)

    @staticmethod
    def event(ctx, kind, *, event_id=None, at=None, source="player_confirmed", **fields):
        e = {key: "" for key in FIELDS}
        e.update(
            schema=1,
            event_id=event_id or digest(uuid.uuid4().hex),
            at=at or utcnow(),
            kind=kind,
            source=source,
            scope="exact",
            **ctx,
        )
        if set(fields) - FIELDS or set(ctx) - {*IDENTITIES, "scenario"}:
            raise HistoryError("Unrecognized history field")
        e.update(fields)
        validate(e)
        return e

    def append(self, *events):
        if not events:
            return ()
        for e in events:
            validate(e)
        with self._file(write=True) as fd:
            return self._append_locked(fd, list(self._read(fd)), events)

    def _append_locked(self, fd, rows, events):
        relations = RelationIndex(rows)
        seen = {r["event_id"]: r for r in rows}
        added = []
        for e in events:
            if e["event_id"] in seen:
                if seen[e["event_id"]] != e:
                    raise HistoryError("History identity collision")
                continue
            validate_relation(e, relations)
            relations.add(e)
            rows.append(e)
            seen[e["event_id"]] = e
            added.append(e)
        data = b"".join(canonical(e) + b"\n" for e in added)
        if len(rows) > MAX_EVENTS or os.fstat(fd).st_size + len(data) > MAX_BYTES:
            raise HistoryError("History limit reached; existing facts retained")
        os.lseek(fd, 0, os.SEEK_END)
        while data:
            written = os.write(fd, data)
            if written <= 0:
                raise HistoryError("History append failed")
            data = data[written:]
        os.fsync(fd)
        _fsync_dir(self.root)
        return tuple(added)

    def once(self, ctx, kind, *, event_id, **fields):
        # Derived observer events may be rediscovered at a later wall clock time.
        with self._file(write=True) as fd:
            rows = list(self._read(fd))
            existing = next((e for e in rows if e["event_id"] == event_id), None)
            if existing and ("at" not in fields or kind in ("unknown_exit", "prompt_offered")):
                fields["at"] = existing["at"]
            e = self.event(ctx, kind, event_id=event_id, **fields)
            self._append_locked(fd, rows, (e,))
            return e

    @staticmethod
    def session_event(session):
        ctx = session.get("context", {})
        if any(not re.fullmatch("[0-9a-f]{64}", ctx.get(k) or "") for k in IDENTITIES):
            return None
        ctx = {**{k: ctx[k] for k in IDENTITIES}, "scenario": ""}
        process = session.get("process")
        if process:
            if (
                type(process.get("pid")) is not int
                or process["pid"] <= 0
                or type(process.get("birth_us")) is not int
                or process["birth_us"] <= 0
                or process.get("executable") != session.get("executable")
            ):
                raise HistoryError("Unbound process history")
            identity = digest("process", ctx["game_executable_sha256"], process)
            if (
                abs(instant(session["started_at"]).timestamp() - process["birth_us"] / 1000000)
                > 0.001
            ):
                raise HistoryError("Process start timestamp does not match its incarnation")
            return PlayHistoryStore.event(
                ctx,
                "process_confirmed",
                event_id=digest(identity, "confirmed"),
                attempt_id=identity,
                at=session["started_at"],
                source="observed",
            )
        identity = digest("launch", ctx, session["started_at"])
        return PlayHistoryStore.event(
            ctx,
            "launch_attempted",
            event_id=identity,
            attempt_id=identity,
            at=session["started_at"],
            source="observed",
        )

    def observe_session(self, session):
        event = self.session_event(session)
        if event:
            self.append(event)
        return event

    @staticmethod
    def issue_event(value):
        ctx = {**{k: value[k] for k in IDENTITIES}, "scenario": ""}
        kind = "crash_unknown" if value["kind"] == "crash" else "resource_stop"
        identity = digest("incident", value["incident_id"])
        return PlayHistoryStore.event(
            ctx,
            kind,
            event_id=identity,
            incident_id=value["incident_id"],
            attempt_id=digest("unbound", value["incident_id"]),
            at=value["occurred_at"],
            source="historical",
        )

    def import_issue(self, value):
        event = self.issue_event(value)
        self.append(event)
        return event

    def link(self, ctx, url, *, scope="exact", source="player_confirmed"):
        issue_number(url)
        return self.once(
            ctx,
            "issue_link",
            event_id=digest("link", ctx, url, scope),
            issue_url=url,
            scope=scope,
            source=source,
        )

    def refresh_issues(self, events, *, runner=None, at=None):
        """Explicit bounded GETs only; no game facts or private identities sent."""
        from .crash_reporting import bounded_command_runner

        runner = runner or bounded_command_runner
        checked = at or utcnow()
        links = {
            (tuple(e[k] for k in (*IDENTITIES, "scenario")), e["issue_url"], e["scope"]): e
            for e in events
            if e["kind"] == "issue_link"
        }
        if len({e["issue_url"] for e in links.values()}) > 32:
            raise HistoryError("Refresh at most 32 linked issues at a time")
        states = {}
        for url in dict.fromkeys(e["issue_url"] for e in links.values()):
            number = issue_number(url)
            state = "unavailable"
            try:
                response = runner(
                    (
                        "gh",
                        "api",
                        "--hostname",
                        "github.com",
                        "--method",
                        "GET",
                        f"repos/{REPOSITORY}/issues/{number}",
                    ),
                    stdin=None,
                    timeout=10.0,
                    max_output_bytes=262144,
                )
                if (
                    response.returncode == 0
                    and type(response.stdout) is bytes
                    and len(response.stdout) <= 262144
                ):
                    value = decode(response.stdout)
                    if (
                        value.get("html_url") == url
                        and value.get("number") == number
                        and value.get("state") in ("open", "closed")
                        and "pull_request" not in value
                    ):
                        state = value["state"]
            except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
                pass
            states[url] = state
        snapshots = []
        for e in links.values():
            ctx = {k: e[k] for k in (*IDENTITIES, "scenario")}
            snapshots.append(
                self.event(
                    ctx,
                    "issue_state",
                    at=checked,
                    source="observed",
                    issue_url=e["issue_url"],
                    issue_state=states[e["issue_url"]],
                    scope=e["scope"],
                )
            )
        self.append(*snapshots)
        return states


@dataclass(frozen=True)
class HistorySummary:
    light: str
    label: str
    opens: int
    started: int
    completed: int
    crashes: int
    last: str
    issue: str
    fix: str
    test: str
    events: tuple


def issue_readback(events, now=None):
    now = now or datetime.now(timezone.utc)
    result = []
    links = {}
    for e in events:
        if e["kind"] == "issue_link":
            links[(e["issue_url"], e["scenario"], e["scope"])] = e
    for (url, scenario, scope), link in links.items():
        snapshots = [
            e
            for e in events
            if e["kind"] == "issue_state"
            and e["issue_url"] == url
            and e["scenario"] == scenario
            and e["scope"] == scope
        ]
        known = [e for e in snapshots if e["issue_state"] in ("open", "closed")]
        label = known[-1]["issue_state"].capitalize() if known else "Unknown"
        if not snapshots:
            label += " · not checked"
        elif snapshots[-1]["issue_state"] == "unavailable":
            label += " · unavailable"
        elif now - instant(snapshots[-1]["at"]) > timedelta(hours=24):
            label += " · stale"
        result.append(
            dict(
                url=url,
                scenario=scenario,
                scope=scope,
                label=label,
                checked_at=known[-1]["at"] if known else "",
            )
        )
    return tuple(
        sorted(result, key=lambda item: (item["scope"] != "exact", item["scenario"], item["url"]))
    )


def summarize(events, ctx, *, now=None):
    rows = tuple(e for e in events if same_map(e, ctx))
    # Ledger order preserves confirmations made later about earlier observations.
    failures = {}
    for e in rows:
        if e["kind"] in FAILURES:
            failures[e["incident_id"]] = e
    by_id = {e["event_id"]: e for e in rows}
    tests_by_incident = {}
    for e in rows:
        if e["kind"] in ("fix_test_passed", "fix_test_failed"):
            target = by_id.get(e["target_id"])
            if target:
                tests_by_incident[
                    (target["incident_id"], target["scenario"], target["checkpoint"])
                ] = e
    unresolved = [
        e
        for e in failures.values()
        if tests_by_incident.get((e["incident_id"], e["scenario"], e["checkpoint"]), {}).get("kind")
        != "fix_test_passed"
    ]
    opens = {e["attempt_id"] for e in rows if e["kind"] == "process_confirmed"}
    started = {e["game_id"] for e in rows if e["kind"] == "game_started"}
    completed = {e["game_id"] for e in rows if e["kind"] in ("retired", "goals_met")}
    checkpoints = [
        e
        for e in rows
        if e["kind"] in CHECKPOINTS + ("process_confirmed", "launch_attempted", "unknown_exit")
    ]
    latest = max(checkpoints, key=lambda e: instant(e["at"]), default=None)
    light, label = "grey", "Not completed" if opens else "Never opened"
    if completed:
        light, label = "green", "Completed"
    if latest and latest["kind"] == "unknown_exit" and not completed:
        label = "Outcome unknown"
    if not opens and latest and latest["kind"] == "launch_attempted":
        label = "Start unconfirmed"
    if unresolved:

        def recovered(failure):
            return any(
                e["kind"] in SUCCESS
                and instant(e["at"]) > instant(failure["at"])
                and (not failure["scenario"] or e["scenario"] == failure["scenario"])
                for e in rows
            )

        red = [e for e in unresolved if e["kind"] != "resource_stop" and not recovered(e)]
        failure = max(red or unresolved, key=lambda e: instant(e["at"]))
        light = "red" if red else "amber"
        label = (
            "Past crash"
            if recovered(failure) and failure["kind"] != "resource_stop"
            else LABELS[failure["kind"]]
        )
    issues = issue_readback(rows, now)
    issue = "No linked issue"
    if issues:
        first = issues[0]
        issue = (
            "Related " if first["scope"] == "related" else ""
        ) + f"#{issue_number(first['url'])} · {first['label']}"
        if len(issues) > 1:
            issue += f" · +{len(issues) - 1}"
    fixes = {}
    for e in rows:
        if e["kind"] in ("fix_proposed", "fix_implemented"):
            fixes[e["fix_id"]] = e
    fix = (
        "No fix recorded"
        if not fixes
        else "Implemented"
        if all(e["kind"] == "fix_implemented" for e in fixes.values())
        else "Proposed / mixed"
        if len(fixes) > 1
        else "Proposed"
    )
    tests = [e for e in rows if e["kind"] in ("fix_test_passed", "fix_test_failed")]
    test = "Not tested"
    if tests:
        outcomes = {(e["fix_id"], by_id[e["target_id"]]["incident_id"]): e["kind"] for e in tests}
        tested_fixes = {e["fix_id"] for e in tests}
        test = (
            "Failed"
            if "fix_test_failed" in outcomes.values()
            else "Passed"
            if len(tested_fixes) == len(fixes) and not unresolved
            else "Partly tested"
        )
    last = LABELS[latest["kind"]] if latest else "None recorded"
    if latest and latest["scenario"]:
        last += " · " + PurePosixPath(latest["scenario"]).stem
    return HistorySummary(
        light,
        label,
        len(opens),
        len(started),
        len(completed),
        sum(e["kind"].startswith("crash_") for e in failures.values()),
        last,
        issue,
        fix,
        test,
        rows,
    )

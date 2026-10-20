"""Cooperative FIFO admission for finite game trials, never a game launcher.

The shared ledger survives a waiter or owner crash. An active lease never expires
automatically: its owner must prove cleanup before another trial can acquire it.
Every caller must use the same path and still apply its existing runtime guards.
This does not prevent a human or unrelated program launching outside the queue.
"""

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

from active_job_control import (
    ControlRefused,
    decode,
    read_plain,
    replace_state,
    require,
    state_lock,
)


def game_is_free(installation, processes):
    """Read-only census; any pre-existing game belongs to someone else.

    SteamInstallation.game_running returns None on uncertain inspection. Never
    infer availability from window focus, idle time, or a missing screenshot.
    Separate profile-restoration readiness remains the finite owner's check.
    """
    running = installation.game_running()
    require(type(running) is bool, "Game process availability is unknown")
    return not running and not processes.games()


class GameQueue:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def locked(self):
        with state_lock(self.path):
            if not self.path.exists():
                initial = {"version": 1, "tickets": [], "active": None}
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(json.dumps(initial).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
            before = read_plain(self.path)
            value = decode(before)
            require(
                set(value) == {"version", "tickets", "active"} and value["version"] == 1,
                "Unknown game queue schema",
            )
            tickets = value["tickets"]
            require(type(tickets) is list, "Invalid queue tickets")
            names = []
            for ticket in tickets:
                require(
                    type(ticket) is dict
                    and set(ticket) == {"id", "owner", "status"}
                    and ticket["status"] in {"waiting", "active", "done", "cancelled"},
                    "Invalid game ticket",
                )
                self.identifiers(ticket["id"], ticket["owner"])
                names.append(ticket["id"])
            require(len(names) == len(set(names)), "Duplicate game ticket")
            active = value["active"]
            active_tickets = [t for t in tickets if t["status"] == "active"]
            if active is None:
                require(not active_tickets, "Missing active lease")
            else:
                require(
                    type(active) is dict
                    and set(active) == {"ticket", "owner", "nonce"}
                    and type(active["nonce"]) is str
                    and re.fullmatch(r"[0-9a-f]{32}", active["nonce"]) is not None
                    and active_tickets
                    == [{"id": active["ticket"], "owner": active["owner"], "status": "active"}],
                    "Invalid active lease",
                )
            yield value
            if decode(before) != value:
                replace_state(self.path, before, value)

    @staticmethod
    def identifiers(ticket, owner):
        require(
            all(
                type(x) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", x) for x in (ticket, owner)
            ),
            "Exact queue ticket and owner required",
        )

    def enqueue(self, ticket, owner):
        self.identifiers(ticket, owner)
        with self.locked() as state:
            existing = [t for t in state["tickets"] if t["id"] == ticket]
            if existing:
                require(existing[0]["owner"] == owner, "Ticket owner mismatch")
                return existing[0]["status"]
            state["tickets"].append({"id": ticket, "owner": owner, "status": "waiting"})
        return "waiting"

    def claim(self, ticket, owner, available):
        """available must freshly verify no game and no unresolved cleanup.

        False means busy; exceptions or unknown results refuse. A lease is only
        queue admission, not permission to launch. Call the finite owner's actual
        admission and process checks again immediately before each mutation.
        """
        with self.locked() as state:
            matches = [t for t in state["tickets"] if t["id"] == ticket]
            require(len(matches) == 1 and matches[0]["owner"] == owner, "Unknown ticket owner")
            require(matches[0]["status"] == "waiting", "Ticket is not waiting")
            waiting = [t for t in state["tickets"] if t["status"] == "waiting"]
            if state["active"] is not None or waiting[0]["id"] != ticket:
                return None
            free = available()
            require(type(free) is bool, "Game availability is unknown")
            if not free:
                return None
            lease = {"ticket": ticket, "owner": owner, "nonce": uuid.uuid4().hex}
            state["active"] = lease
            matches[0]["status"] = "active"
        return lease

    def guard(self, lease):
        with self.locked() as state:
            require(state["active"] is not None and state["active"] == lease, "Stale game lease")

    def release(self, lease, cleanup_verified):
        """The real owner checks stop, restoration and terminal finalization.

        Caller-supplied callback is trusted local code, not proof authenticated by
        this queue. Failure retains the lease and blocks every successor.
        """
        with self.locked() as state:
            require(state["active"] is not None and state["active"] == lease, "Stale game lease")
            require(cleanup_verified() is True, "Game cleanup is not verified")
            ticket = next(t for t in state["tickets"] if t["id"] == lease["ticket"])
            ticket["status"] = "done"
            state["active"] = None

    def cancel_waiter(self, ticket, owner):
        with self.locked() as state:
            matches = [t for t in state["tickets"] if t["id"] == ticket]
            require(len(matches) == 1 and matches[0]["owner"] == owner, "Unknown ticket owner")
            require(
                matches[0]["status"] == "waiting", "Active or settled ticket cannot be cancelled"
            )
            matches[0]["status"] = "cancelled"

    def wait(self, ticket, owner, available, *, cancelled, deadline, interval=2):
        """Wait in a local process; no model calls or GUI polling are needed.

        Deadline is for this waiter only. It cannot renew or expire a live owner.
        On timeout/cancellation, caller explicitly cancels its waiting ticket.
        """
        require(type(interval) in (int, float) and 0 < interval <= 10, "Invalid wait interval")
        require(type(deadline) in (int, float) and math.isfinite(deadline), "Finite wait required")
        while time.monotonic() < deadline:
            if cancelled():
                return None

            def still_available():
                free = available()
                require(type(free) is bool, "Game availability is unknown")
                return free and not cancelled() and time.monotonic() < deadline

            try:
                lease = self.claim(ticket, owner, still_available)
            except ControlRefused as exc:
                # The common lock is nonblocking. Only ordinary contention is
                # retryable; corruption, identity or availability failures escape.
                if str(exc) != "State writer lock unavailable":
                    raise
                lease = None
            if lease:
                return lease
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        return None

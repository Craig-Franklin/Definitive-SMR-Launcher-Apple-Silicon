"""Unchanged accepted initial successful-sample tick fence."""

import time


def _wait_initial_sample_tick(packet, expected, process, ready, bind_deadline, poll=None):
    from individual_stop import read_receipt, _number

    sample, sample_fp = read_receipt(packet / "sample.json")
    sample_keys = {
        "version",
        "job",
        "nonce",
        "code",
        "anchor",
        "process",
        "seq",
        "status",
        "start",
        "complete",
    }
    assert set(sample) == sample_keys and type(sample["version"]) is int and sample["version"] == 1
    assert all(sample[k] == expected[k] for k in ("job", "nonce", "code", "anchor"))
    assert sample["process"] == process and type(sample["process"]["pid"]) is int
    assert (
        type(sample["seq"]) is int and 0 <= sample["seq"] < 2**63 and sample["status"] == "success"
    )
    assert all(_number(sample[k]) for k in ("anchor", "start", "complete"))
    assert expected["anchor"] <= sample["start"] <= sample["complete"] <= time.monotonic()
    assert type(ready["state_seq"]) is int and ready["state_seq"] > 0 and _number(ready["observed"])
    previous = None
    while True:
        if poll is not None:
            poll()
        now = time.monotonic()
        assert (
            _number(now)
            and now < min(bind_deadline, expected["anchor"] + 540)
            and now - sample["start"] < 60
        ), "initial sample fence expired"
        state, fp = read_receipt(packet / "state.json")
        observed_at = time.monotonic()
        assert set(state) == set(expected) | {"version", "kind", "state_seq", "observed", "state"}
        assert type(state["version"]) is int and state["version"] == 1 and state["kind"] == "state"
        assert all(state[k] == v for k, v in expected.items()) and _number(state["anchor"])
        assert type(state["state_seq"]) is int and state["state_seq"] >= ready["state_seq"]
        assert _number(state["observed"]) and ready["observed"] <= state["observed"] <= observed_at
        assert observed_at - state["observed"] <= 3, "stale initial sample fence"
        detail = state["state"]
        assert type(detail) is dict and (detail.get("phase"), detail.get("reason")) in {
            ("ready", "observer_ready"),
            ("awaiting_binding", "descriptor_pending"),
        }
        assert all(
            type(detail.get(k)) is bool
            for k in (
                "ready",
                "bound",
                "terminal",
                "pending_launch",
                "requires_root_reconciliation",
                "gui_grant",
                "restoration_grant",
                "new_job_grant",
            )
        )
        assert detail == dict(
            phase=detail["phase"],
            reason=detail["reason"],
            ready=True,
            bound=False,
            terminal=False,
            process=None,
            pending_launch=True,
            requires_root_reconciliation=True,
            gui_grant=False,
            restoration_grant=False,
            new_job_grant=False,
        )
        if previous is not None:
            old, old_fp = previous
            assert (
                state["state_seq"] >= old["state_seq"] and state["observed"] >= old["observed"]
            ), "backwards initial sample fence"
            assert state["state_seq"] != old["state_seq"] or fp == old_fp, (
                "changed initial sample fence"
            )
        previous = state, fp
        assert read_receipt(packet / "sample.json")[1] == sample_fp, (
            "initial sample changed during fence"
        )
        now = time.monotonic()
        assert (
            _number(now)
            and observed_at <= now < min(bind_deadline, expected["anchor"] + 540)
            and now - sample["start"] < 60
        ), "initial sample fence expired"
        if (
            state["state_seq"] > ready["state_seq"]
            and state["observed"] >= sample["complete"]
            and detail["phase"] == "awaiting_binding"
        ):
            return
        # Poll the existing tick channel only; every read error or corruption escapes.
        time.sleep(min(0.05, bind_deadline - now, 60 - (now - sample["start"])))

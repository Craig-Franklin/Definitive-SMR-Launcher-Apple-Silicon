"""Settle one finite operation through durable outcome, exact stop and restoration.

The admitted adapter owns concrete execution, identity checks and fixed deadlines.
This shared lifecycle grants no runtime admission and makes no game success claim.
"""

from active_job_control import UserCancelled, require as need


def cancelled_outcome():
    return dict(
        status="safety_stop",
        cause="user_requested_game_handback",
        world_loaded="unknown",
        gameplay_credit=False,
        save_reopen_credit=False,
    )


def run_calibration(adapter):
    """One complete invocation; no later model callback supplies cleanup."""
    outcome = {"status": "automation_failed", "cause": "preparation_not_finished"}
    result = dict(
        outcome=outcome, stopped=False, restored=False, terminal_finished=False, errors=[]
    )
    try:
        adapter.prepare()
        adapter.launch()
        outcome = adapter.observe()
    except UserCancelled:
        outcome = cancelled_outcome()
    except BaseException as exc:
        outcome = {"status": "automation_failed", "cause": type(exc).__name__ + ": " + str(exc)}
        result["errors"].append("operation: " + outcome["cause"])
    result["outcome"] = outcome
    durable = False
    try:
        need(adapter.persist_outcome(outcome) is not False, "Outcome persistence refused")
        durable = True
    except BaseException as exc:
        result["errors"].append("outcome persistence: " + str(exc))
    # Stop is unconditional, including torn/full-disk outcome persistence.
    try:
        result["stopped"] = adapter.stop() is True
        need(result["stopped"], "Exact stop refused")
    except BaseException as exc:
        result["errors"].append("stop: " + str(exc))
    if durable and result["stopped"]:
        try:
            result["restored"] = adapter.restore() is True
            need(result["restored"], "Restoration refused")
        except BaseException as exc:
            result["errors"].append("restoration: " + str(exc))
    # Operation errors remain in the result and immutable raw outcome.
    # Verified cleanup permits recording an honest failed terminal disposition.
    if durable and result["stopped"] and result["restored"]:
        try:
            need(
                adapter.finish(outcome, result["stopped"], result["restored"]) is not False,
                "Terminal indexing refused",
            )
            result["terminal_finished"] = True
        except BaseException as exc:
            result["errors"].append("terminal indexing: " + str(exc))
    return result

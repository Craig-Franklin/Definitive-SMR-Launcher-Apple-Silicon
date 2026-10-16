"""Source-only observation seam for the existing finite lifecycle interface.

Install this as the admitted owner's observe() implementation, then use the
existing run_calibration(adapter) contract for outcome/stop/restore/finish. This
module has no runnable entry point, GUI transport, authority grant or retry.
The named owner hooks below must be supplied by a fresh independently reviewed
finite operation; absent/unsupported hooks fail through that lifecycle's cleanup.
"""

from dataclasses import asdict
import time

from unique_save_file import (
    bind_stable,
    capture_before,
    inspect_new,
    public_reference,
    finite,
    need,
    require_absent,
    verify_reopen,
)


def observe_save_cycle(
    owner, scope, intent, *, minimum_stable_seconds, deadline, scratch_autosaves=()
):
    """Connect actual filesystem facts to one save and exact guarded reopen.

    owner.guard must authenticate current job/profile/process/availability,
    original deadlines and current observed UI intent/receiver/overwrite refusal.
    Every mutable owner hook is one-use and uses the existing bounded-child and
    cancellation path. wait_stability uses existing ticks and the supplied fixed
    deadline; it cannot renew clocks. stop_for_relaunch must prove exact-owned
    positive absence. owned_reopen_location supplies an observed explicit mapping
    after profile movement, never a digest-selected copy. write uses existing
    exclusive/fsynced private receipts, never a public ledger or save directory.
    None of these contracts is an implementation or proof of game/UI semantics.

    save_once, relaunch_once, reopen_once and the continued-action hook require
    keyword-only validate_delivery and deadline. They must call the validator
    after their own blocking prework, immediately before each mutable delivery,
    and use its returned fresh binding. No unvalidated legacy hook is supported.
    Their existing authenticated owner/availability fence must remain current
    through delivery. Filesystem checks and external GUI/server acceptance are
    not atomic; neither a later failure nor cleanup cancels dispatched input.
    """
    need(
        finite(minimum_stable_seconds) and 0 < minimum_stable_seconds <= 30,
        "Finite stability interval required",
    )
    need(
        finite(deadline) and time.monotonic() + minimum_stable_seconds < deadline,
        "Original save-cycle deadline unavailable",
    )
    owner.guard("save", scope, intent)
    before = capture_before(scope, intent)
    owner.write("save-before.json", asdict(before))

    def validate_save_delivery():
        owner.guard("save", scope, intent)
        # Guard and receipt persistence may block or change the filesystem.
        require_absent(before)
        need(time.monotonic() < deadline, "Original save delivery deadline expired")
        return intent

    validate_save_delivery()
    owner.save_once(intent, validate_delivery=validate_save_delivery, deadline=deadline)
    first = inspect_new(before)
    owner.write("save-first-sample.json", asdict(first))
    need(
        first.inventory.complete + minimum_stable_seconds < deadline,
        "Original stability window expired",
    )
    owner.wait_stability(first.inventory.complete + minimum_stable_seconds, deadline)
    bound = bind_stable(
        before, first, minimum_stable_seconds=minimum_stable_seconds, deadline=deadline
    )
    owner.write("save-bound-file.json", asdict(bound))
    # This is a private partial observation, not a historical save-made claim.
    owner.write("save-before-relaunch.json", public_reference(bound))
    need(owner.stop_for_relaunch() is True, "Exact stop before relaunch refused")
    current_scope, mapping = owner.owned_reopen_location(bound)

    def verify_bound():
        return verify_reopen(
            bound,
            current_scope,
            deadline=deadline,
            mapping=mapping,
            scratch_autosaves=scratch_autosaves,
        )

    def validate_bound_delivery(phase):
        checked = verify_bound()
        owner.guard(phase, current_scope, checked)
        # Authenticate the same binding again after a potentially blocking guard.
        # No receipt write follows this read before entering the delivery hook.
        return verify_bound()

    checked = verify_bound()
    owner.write("save-after-stop-identity.json", checked)
    validate_bound_delivery("relaunch")
    owner.relaunch_once(
        validate_delivery=lambda: validate_bound_delivery("relaunch"), deadline=deadline
    )
    checked = verify_bound()
    owner.write("save-before-reopen-identity.json", checked)
    checked = validate_bound_delivery("reopen")
    owner.reopen_once(
        checked, validate_delivery=lambda: validate_bound_delivery("reopen"), deadline=deadline
    )
    validate_bound_delivery("continued-action")
    observations = owner.observe_reopened_world_and_continued_action(
        validate_delivery=lambda: validate_bound_delivery("continued-action"), deadline=deadline
    )
    owner.write("save-cycle-observations.json", observations)
    owner.write(
        "save-after-reopen-identity.json",
        verify_bound(),
    )
    return dict(
        status="safety_stop",
        cause="save_file_identity_bound_empirical_cycle_requires_review",
        save_reference=public_reference(bound),
        world_loaded="unknown",
        gameplay_credit=False,
        save_reopen_credit=False,
    )

"""Pure root GUI phase check; never performs an action or grants other gates.

The caller supplies fresh times from the original job's monotonic clock and
persists the returned save entry before requesting the first save action.
Stop and restoration remain independent of this GUI-only check.
"""
import math


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('invalid clock value')
    return value


def admit_action(anchor, now, action, save_started_at=None):
    """Return the unchanged save entry, or the first save entry; deny otherwise.

    Construction, navigation, initial loading and calibration use ``game``.
    After ``save_begin``, only named-save/reload verification and the planned
    continued-operation check are permitted. This is an additional root gate;
    exact identities, helper liveness, monitoring and frozen inputs still apply.
    """
    anchor, now = _time(anchor), _time(now)
    if now < anchor or now - anchor >= 540:
        raise ValueError('outside GUI phase')
    if save_started_at is not None:
        entry = _time(save_started_at)
        if entry < anchor or entry > now or entry - anchor > 360:
            raise ValueError('invalid save entry')
    if action == 'game' and save_started_at is None and now - anchor < 240:
        return None
    if action == 'save_begin' and save_started_at is None:
        if now - anchor <= 360 and anchor + 540 - now >= 180:
            return now
        raise ValueError('save entry deadline or reserve exceeded')
    if action in ('save', 'reload', 'verify', 'continue') and save_started_at is not None:
        return save_started_at
    raise ValueError('action not permitted in this phase')

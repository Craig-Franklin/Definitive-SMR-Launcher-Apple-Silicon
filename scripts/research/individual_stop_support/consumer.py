"""Root-only pure individual-job gate. No GUI, process or restoration actions.

The caller supplies fresh independent OS observations and the unchanged watchdog
binding. Files alone never establish liveness. This gate supplements the existing
ownership, capacity, resource, freeze and profile/save guards.
"""
import hashlib
import json
import math


class Denied(ValueError):
    pass


def number(n):
    return type(n) in (int, float) and math.isfinite(n)


def require(condition, reason):
    if not condition:
        raise Denied(reason)


def permit(expected, state, bound, now, helper_expected, helper_current,
           game_current, watchdog_process, sample, code_ok, freeze_ok,
           domain_verified, previous=None):
    """Validate one action, returning replay history, never a durable GUI grant."""
    require(number(now) and number(expected['anchor']), 'invalid clock')
    require(expected['anchor'] <= now < expected['anchor'] + 540,
            'outside individual observation phase')
    require(code_ok is True and freeze_ok is True and domain_verified is True,
            'code/freeze/clock unverified')
    require(helper_current is not None and helper_current == helper_expected,
            'helper absent or incarnation changed')
    for value, kind in ((state, 'state'), (bound, 'bound')):
        require(type(value) is dict and value.get('version') == 1 and
                value.get('kind') == kind, 'invalid envelope')
        require(all(value.get(k) == v for k, v in expected.items()),
                'admission identity changed')
        require(type(value.get('state_seq')) is int and value['state_seq'] > 0,
                'invalid sequence')
        observed = value.get('observed')
        require(number(observed) and 0 <= now - observed <= 3,
                'stale or future helper state')
        detail = value.get('state')
        require(type(detail) is dict and detail.get('phase') == 'bound' and
                detail.get('reason') == 'holding_exact_identity' and
                detail.get('bound') is True and detail.get('ready') is True and
                detail.get('terminal') is False, 'helper not holding identity')
        require(all(detail.get(k) is False for k in
                    ('gui_grant', 'restoration_grant', 'new_job_grant')),
                'unexpected authority')
    require(state['state_seq'] == bound['state_seq'] and
            state['observed'] == bound['observed'] and
            state['state'] == bound['state'], 'partial publication')
    process = state['state']['process']
    require(type(process) is dict and set(process) == {'pid', 'birth', 'executable'}
            and type(process['pid']) is int and process['pid'] > 0 and
            type(process['birth']) is str and process['birth'].isdecimal() and
            str(int(process['birth'])) == process['birth'] and
            int(process['birth']) > 0 and
            process['executable'] == expected['expected_executable'],
            'invalid game identity')
    require(process == game_current == watchdog_process,
            'game/watchdog identity not independently confirmed')
    require(type(sample) is dict and sample.get('version') == 1 and
            sample.get('status') == 'success' and sample.get('process') == process
            and all(sample.get(k) == expected[k] for k in
                    ('job', 'nonce', 'code', 'anchor')),
            'independent completed monitor missing')
    start, complete = sample.get('start'), sample.get('complete')
    require(number(start) and number(complete) and
            expected['anchor'] <= start <= complete <= now and now-start < 60,
            'monitor stale/incomplete/future')
    fingerprint = hashlib.sha256(json.dumps(state, sort_keys=True,
                                           allow_nan=False).encode()).hexdigest()
    history = {'seq': state['state_seq'], 'observed': state['observed'],
               'sha256': fingerprint, 'now': now}
    if previous is not None:
        require(now >= previous['now'] and history['seq'] >= previous['seq'] and
                history['observed'] >= previous['observed'], 'replayed state')
        require(history['seq'] != previous['seq'] or
                history['sha256'] == previous['sha256'], 'rewritten sequence')
    return history

"""Audit executed repairs and proven pre-launch refusals as different trace types.

No refusal fabricates an action timestamp or a recovery window. Unknown errors,
partial execution and unconfirmed reset still reject the record.
"""
import json
import math
from pathlib import Path

import audit_main_development as executed_audit
import reuse_flow_executor as ex


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def refusal_checks(record, timing):
    failures = []
    def require(value, reason):
        if not value:
            failures.append(reason)
    r = record; reason = r.get('collection_or_setup_error', '')
    pre = r.get('non_target_immediately_before_action') or {}
    observed = r.get('pre_action_policy_observation') or {}
    boundary = r.get('decision_execution_boundary') or {}
    ended = r.get('outcome_fixed_at')
    def age_exceeded(value, limit):
        return finite(value) and finite(ended) and ended - value > limit
    proven = False; category = None
    if reason == 'Decision execution boundary blocked candidate':
        category = 'decision_boundary_refusal'; proven = boundary.get('passed') is False
    elif reason.startswith('Common safety gate blocked the candidate:'):
        category = 'common_gate_refusal'
        proven = r.get('common_gate_blocked') is True and r.get('common_safety_gate', {}).get('passed') is False
    elif reason == 'Pre-action policy observation became stale before launch':
        category = 'stale_launch_observation'; proven = age_exceeded(observed.get('started_at'), 30)
    elif reason == 'Synchronous pre-action gate became stale before launch':
        category = 'stale_synchronous_check'
        proven = age_exceeded(pre.get('completed_at'), timing['launch_after_pre_check_max_seconds'])
    elif reason == 'Validation or permission expired at actual action launch':
        category = 'expired_launch_permission'; proven = age_exceeded(boundary.get('permission_valid_until'), 0)
    elif reason == 'Pre-action evidence exceeded its fixed age budget':
        category = 'stale_non_target_check'
        proven = age_exceeded(pre.get('started_at'), timing['pre_check_max_age'])
    elif reason == 'Pre-action instability: no candidate launched':
        category = 'non_target_prelaunch_refusal'
        proven = r.get('pre_action_instability') is True and pre.get('passed') is False
    require(proven, 'No independently backed recognised pre-launch refusal')
    require(r.get('type') == 'pre-main_executor_development' and r.get('main_trials') == 0
            and r.get('registered') is False and r.get('arm') == 'repair', 'raw executor counter/type identity')
    require(r.get('action_executed') is False, 'explicitly no candidate execution')
    require(not any(k in r for k in ('action_started_at', 'action_start_seconds', 'execution')),
            'no action start or runner evidence may be discarded')
    require(r.get('execution_complete') is None and r.get('target_recovery') is None
            and r.get('non_target_regression') is None
            and r.get('repair_outcome') == 'Not executed', 'refusal is not an executed success/failure')
    require(r.get('post_reset_confirmed') is True and not r.get('reset_error')
            and not r.get('termination_error'), 'separately confirmed safe final reset')
    require(finite(ended) and finite(r.get('completed_at')) and ended <= r['completed_at'],
            'refusal outcome recorded before reset completion')
    return {'accepted': not failures, 'failures': failures, 'blocked_before_action': not failures,
            'launch_refusal_category': category, 'repair_outcome': r.get('repair_outcome')}


def audit(path):
    path = Path(path).resolve(); r = json.loads(path.read_text()); out = path.parent.parent
    p = json.loads((out/'protocol.json').read_text())
    if r.get('action_executed') is True or r.get('arm') == 'no-repair':
        try:
            answer = executed_audit.audit(path)
        except (KeyError, TypeError, ValueError, StopIteration) as error:
            answer = {'accepted': False, 'failures': ['Incomplete executed trace: '+str(error)]}
        answer['blocked_before_action'] = False
        return answer
    answer = refusal_checks(r, p['timing']); failures = answer['failures']
    if (path.parent/'runner').exists():
        failures.append('Runner directory exists; no-execution claim is not proven')
    if r.get('protocol_sha256') != ex.sha(out/'protocol.json'):
        failures.append('protocol identity')
    if r.get('candidate_sha256') != p['candidate_hashes'].get(r.get('candidate')):
        failures.append('candidate identity')
    for name in ('initial-reset.log', 'post-reset.log'):
        log = path.parent/name
        if not log.exists() or 'baseline confirmed' not in log.read_text():
            failures.append(name+' proof')
    freeze = json.loads((out/'executor-freeze.json').read_text())
    for name, digest in freeze['tool_hashes'].items():
        if ex.sha(out/'tool-snapshot'/name) != digest:
            failures.append('archived source identity '+name)
    answer.update(accepted=not failures, blocked_before_action=not failures,
        record=str(path.relative_to(ex.ROOT)), record_sha256=ex.sha(path),
        protocol=str((out/'protocol.json').relative_to(ex.ROOT)), protocol_sha256=ex.sha(out/'protocol.json'),
        main_trials=0, development_only=True)
    return answer

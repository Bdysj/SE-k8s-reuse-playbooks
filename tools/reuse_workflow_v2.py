"""Prospective v2 bounded RQ2 workflow; validation freshness checked through permission; callbacks supply observations and isolated replay.

This module never executes a candidate or alters registered history. The caller
must check the returned revision and common gate again at the execution boundary.
An unavailable validation callback is recorded as unavailable, never as success.
"""
import copy
import json
import time
from pathlib import Path

import reuse_assessor as a


def save_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not replace another assessment or an earlier process history.
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def evaluate(candidate, candidate_hash, current, history, collect, safety_gate,
             *, config, validate=None, omitted=None, clock=time.time,
             monotonic=time.monotonic, compare=a.compare):
    required = {'observation_ttl', 'refresh_budget', 'replay_budget',
                'total_budget_seconds', 'max_assessments'}
    if set(config) != required:
        raise ValueError('All workflow budgets must be declared explicitly')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0
           for v in config.values()):
        raise ValueError('Workflow budgets must be finite nonnegative numbers')
    for key in ('refresh_budget', 'replay_budget', 'max_assessments'):
        if not isinstance(config[key], int):
            raise ValueError('Count budgets must be integers')
    if config['max_assessments'] < 1:
        raise ValueError('At least one assessment must be permitted')
    import math
    if not all(math.isfinite(v) for v in config.values()):
        raise ValueError('Workflow budgets must be finite')
    history_hash = a.digest(history)
    result = {'type': 'rq2_development_workflow', 'candidate': candidate,
              'candidate_sha256': candidate_hash, 'contract_sha256': a.digest(current),
              'registered_history_sha256': history_hash, 'main_trials': 0,
              'started_at': clock(), 'configuration': copy.deepcopy(config),
              'omitted_condition': omitted, 'assessments': [], 'events': [],
              'initial_decision': None, 'final_decision': None,
              'execution_route': 'No candidate execution', 'execution_permitted': False,
              'refreshes': 0, 'replays': 0, 'validation_certificate': None}
    deadline = monotonic() + config['total_budget_seconds']
    snapshot = None
    validation = None
    def observe(reason):
        try:
            value = collect(candidate, copy.deepcopy(current))
            if not isinstance(value, dict):
                raise ValueError('Collector did not return an observation record')
            result['events'].append({'kind': 'observation', 'reason': reason,
                                     'at': clock(), 'sha256': a.digest(value)})
            return copy.deepcopy(value)
        except Exception as error:
            result['events'].append({'kind': 'observation_unavailable',
                                     'reason': reason, 'at': clock(), 'error': str(error)})
            return {'completed_at': clock()}
    while len(result['assessments']) < config['max_assessments']:
        if monotonic() >= deadline:
            result['final_decision'] = 'Stop reuse'
            result['stop_reason'] = 'Overall decision budget exhausted'
            break
        if snapshot is None:
            snapshot = observe('initial observation')
        remaining = config['refresh_budget']-result['refreshes'] + config['replay_budget']-result['replays']
        states = compare(candidate, candidate_hash, snapshot, current,
                           now=clock(), ttl=config['observation_ttl'], validation=validation)
        assessed = {**a.decision(states, remaining, omitted), 'states': states,
                    'at': clock(), 'observation_sha256': a.digest(snapshot),
                    'relevant_revision': snapshot.get('relevant_revision')}
        result['assessments'].append(copy.deepcopy(assessed))
        if result['initial_decision'] is None:
            result['initial_decision'] = assessed['decision']
        result['final_decision'] = assessed['decision']
        if assessed['decision'] == 'Stop reuse':
            result['stop_reason'] = assessed['reason']
            break
        if assessed['decision'] == 'Direct reuse':
            # A fresh dependency observation is mandatory even after replay.
            fresh = observe('immediately before execution permission')
            stamp = fresh.get('started_at')
            fresh_enough = (isinstance(stamp, (int, float))
                            and 0 <= clock()-stamp <= config['observation_ttl'])
            same_revision = (snapshot.get('relevant_revision') is not None
                             and snapshot['relevant_revision'] == fresh.get('relevant_revision'))
            if not (fresh_enough and same_revision):
                snapshot = fresh
                validation = None
                result['events'].append({'kind': 'execution_permission_invalidated',
                    'at': clock(), 'fresh': fresh_enough, 'same_revision': same_revision})
                continue
            if monotonic() >= deadline:
                result['final_decision'] = 'Stop reuse'
                result['stop_reason'] = 'Overall decision budget exhausted before safety gate'
                break
            try:
                gate = safety_gate(candidate, fresh)
            except Exception as error:
                gate = {'passed': False, 'failures': ['Common gate unavailable: '+str(error)]}
            result['common_safety_gate'] = copy.deepcopy(gate)
            result['relevant_revision'] = fresh.get('relevant_revision')
            result['permission_observation_started_at'] = stamp
            # The gate itself may take time; expired evidence cannot permit action.
            final_states = compare(candidate, candidate_hash, fresh, current, now=clock(),
                ttl=config['observation_ttl'], validation=validation)
            final_rule = a.decision(final_states, remaining, omitted)
            result['permission_states'] = copy.deepcopy(final_states)
            result['validation_certificate'] = copy.deepcopy(validation)
            result['permission_expires_at'] = min(stamp + config['observation_ttl'],
                validation['completed_at'] + config['observation_ttl'] if validation else float('inf'))
            result['execution_permitted'] = (gate.get('passed') is True
                and final_rule['decision'] == 'Direct reuse'
                and 0 <= clock()-stamp <= config['observation_ttl'] and monotonic() < deadline)
            if final_rule['decision'] != 'Direct reuse':
                result['final_decision'] = final_rule['decision']
                if final_rule['decision'] == 'Revalidate':
                    result['events'].append({'kind':'permission_evidence_expired','at':clock()})
                    snapshot = fresh
                    continue
                result['stop_reason'] = 'Evidence expired or unsupported at permission boundary'
            result['execution_route'] = ('Direct replay of frozen candidate' if result['execution_permitted']
                                         else 'No execution: common gate or freshness budget blocked')
            break
        unresolved = [x['condition'] for x in states if x['state'] == a.UNKNOWN
                      and x['condition'] != omitted]
        # Missing/stale automatic evidence is refreshed before spending a replay.
        missing = all(x['state'] == a.UNKNOWN for x in states)
        if result['refreshes'] < config['refresh_budget'] and (missing or validation is None and result['refreshes'] == 0):
            result['refreshes'] += 1
            snapshot = observe('bounded refresh of unresolved evidence')
            continue
        if result['replays'] >= config['replay_budget'] or validate is None:
            result['final_decision'] = 'Stop reuse'
            result['stop_reason'] = ('Isolated validation adapter unavailable' if validate is None
                                     else 'Unresolved evidence and replay budget exhausted')
            break
        result['replays'] += 1
        try:
            validation = validate(candidate, candidate_hash, copy.deepcopy(snapshot),
                                  copy.deepcopy(current), unresolved, max(0, deadline-monotonic()))
            result['validation_certificate'] = copy.deepcopy(validation)
            result['events'].append({'kind': 'isolated_validation', 'at': clock(),
                                     'evidence': copy.deepcopy(validation)})
        except Exception as error:
            validation = None
            result['events'].append({'kind': 'isolated_validation_unavailable', 'at': clock(),
                                     'error': str(error)})
        # The incident must still have the same relevant revision after replay.
        snapshot = observe('after isolated validation')
    else:
        result['final_decision'] = 'Stop reuse'
        result['stop_reason'] = 'Reassessment count exhausted'
    result['fallback_required'] = not result['execution_permitted']
    if result['execution_permitted'] and result['initial_decision'] == 'Revalidate':
        result['execution_route'] = 'Replay after evidence revalidation'
    result['completed_at'] = clock()
    result['history_preserved'] = a.digest(history) == history_hash
    if not result['history_preserved']:
        raise RuntimeError('Registered history was mutated by the decision workflow')
    return result

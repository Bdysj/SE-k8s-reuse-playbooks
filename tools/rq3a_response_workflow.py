"""Independent RQ3a baseline controllers; no candidate action is performed here.

Fault matching uses only the fitted incident model. Whole-environment comparison
uses its canonical scope and an independently verified actual replica outcome.
Neither baseline consumes proposed requirement states as an admission rule.
Permissions must also pass permission_boundary at the physical launch boundary.
"""
import copy
import math
import time

import comparison_policies as policies
import formal_methods as methods
import reuse_assessor as a

CONDITIONS = ('target_objects', 'referenced_fields', 'resource_relationships',
              'verification_criteria')


def check_config(config):
    required = {'observation_ttl', 'refresh_budget', 'replay_budget',
                'total_budget_seconds', 'max_assessments'}
    if set(config) != required:
        raise ValueError('All fixed decision budgets must be declared')
    if any(isinstance(x, bool) or not isinstance(x, (int, float))
           or not math.isfinite(x) or x < 0 for x in config.values()):
        raise ValueError('Decision budgets must be finite nonnegative numbers')
    if any(not isinstance(config[k], int) for k in
           ('refresh_budget', 'replay_budget', 'max_assessments')):
        raise ValueError('Count budgets must be integers')
    if config['max_assessments'] < 1:
        raise ValueError('At least one assessment required')


def fresh(observed, now, ttl):
    start, end = observed.get('started_at'), observed.get('completed_at')
    return (all(isinstance(x, (int, float)) and not isinstance(x, bool)
                and math.isfinite(x) for x in (start, end))
            and 0 <= now - start <= ttl and start <= end <= now
            and bool(observed.get('relevant_revision')))


def broad_revision(observed, criteria):
    return a.digest({'namespace': policies.canonical(methods.scope_snapshot(observed)),
                     'criteria': criteria})


def certificate_valid(cert, candidate_hash, current, revision, now, ttl, verify):
    """Provenance verification is mandatory; an asserted pass alone is insufficient."""
    if not isinstance(cert, dict) or verify is None:
        return False
    stamp = cert.get('completed_at')
    basic = (cert.get('type') == 'immutable_actual_isolated_outcome_v1'
             and cert.get('candidate_sha256') == candidate_hash
             and cert.get('contract_sha256') == a.digest(current)
             and cert.get('relevant_revision') == revision
             and cert.get('outcome') == 'Successful repair'
             and cert.get('fidelity_passed') is True
             and set(cert.get('verified_requirements', [])) == set(CONDITIONS)
             and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
             and math.isfinite(stamp) and 0 <= now - stamp <= ttl)
    return basic and verify(cert) is True


def evaluate(method, candidate, candidate_hash, current, collect, safety_gate,
             *, config, incident=None, model=None, reference=None, criteria=None,
             validate=None, verify_validation=None, clock=time.time,
             monotonic=time.monotonic):
    if method not in ('fault_matching', 'whole_environment'):
        raise ValueError('This controller implements independent baselines only')
    check_config(config)
    if method == 'fault_matching' and (incident is None or model is None):
        raise ValueError('A frozen model and actual permitted incident are required')
    if method == 'whole_environment' and (reference is None or criteria is None):
        raise ValueError('A fixed registration reference and current criteria are required')
    result = {'type': 'rq3a_baseline_controller_development', 'main_trials': 0,
              'method': method, 'candidate': candidate, 'candidate_sha256': candidate_hash,
              'contract_sha256': a.digest(current), 'configuration': copy.deepcopy(config),
              'started_at': clock(), 'initial_decision': None, 'final_decision': 'Stop reuse',
              'execution_permitted': False, 'execution_route': 'No candidate execution',
              'refreshes': 0, 'replays': 0, 'events': [], 'assessments': [],
              'validation_certificate': None,
              'condition_states_used_for_baseline_admission': False}
    deadline = monotonic() + config['total_budget_seconds']
    cert = None
    def observe(reason):
        try:
            value = collect(candidate, copy.deepcopy(current))
            if not isinstance(value, dict):
                raise ValueError('Observation is not a record')
            result['events'].append({'kind': 'observation', 'reason': reason,
                                     'at': clock(), 'sha256': a.digest(value)})
            return copy.deepcopy(value)
        except Exception as error:
            result['events'].append({'kind': 'observation_unavailable', 'at': clock(),
                                     'reason': reason, 'error': str(error)})
            return {}
    observed = observe('initial permitted incident observation')
    for _ in range(config['max_assessments']):
        if monotonic() >= deadline:
            result['stop_reason'] = 'Overall decision budget exhausted'
            break
        if not fresh(observed, clock(), config['observation_ttl']):
            if result['refreshes'] < config['refresh_budget']:
                result['refreshes'] += 1
                observed = observe('bounded refresh of unavailable automatic observation')
                continue
            result['stop_reason'] = 'Automatic observation unavailable or stale'
            break
        if method == 'fault_matching':
            decision = policies.fault_matching(model, candidate, incident)
            result['policy_input'] = copy.deepcopy(incident)
            result['comparison_model_sha256'] = a.digest(model)
        else:
            decision = policies.whole_environment(reference['snapshot'],
                methods.scope_snapshot(observed), reference['criteria'], criteria)
            result['comparison_reference_sha256'] = a.digest(reference)
            result['current_criteria'] = copy.deepcopy(criteria)
        result['assessments'].append({**decision, 'at': clock(),
            'observation_sha256': a.digest(observed),
            'relevant_revision': observed['relevant_revision']})
        if result['initial_decision'] is None:
            result['initial_decision'] = decision['decision']
        if decision['decision'] == 'Stop reuse':
            result['stop_reason'] = 'Incident similarity below the fixed threshold'
            break
        needs_replay = method == 'whole_environment' and decision['decision'] == 'Revalidate'
        revision = observed['relevant_revision']
        namespace_revision = broad_revision(observed, criteria) if method == 'whole_environment' else None
        if needs_replay:
            if result['replays'] >= config['replay_budget'] or validate is None or verify_validation is None:
                result['stop_reason'] = 'Full revalidation unavailable or budget exhausted'
                break
            result['replays'] += 1
            try:
                cert = validate(candidate, candidate_hash, copy.deepcopy(observed),
                    copy.deepcopy(current), list(CONDITIONS), max(0, deadline - monotonic()))
                result['validation_certificate'] = copy.deepcopy(cert)
                result['events'].append({'kind': 'actual_full_revalidation', 'at': clock(),
                                         'certificate': copy.deepcopy(cert)})
                if not certificate_valid(cert, candidate_hash, current, revision,
                                         clock(), config['observation_ttl'], verify_validation):
                    result['stop_reason'] = 'Full revalidation did not supply a valid confirmed pass'
                    break
            except Exception as error:
                result['events'].append({'kind': 'full_revalidation_unavailable',
                                         'at': clock(), 'error': str(error)})
                result['stop_reason'] = 'Full revalidation or provenance audit unavailable'
                break
        current_observation = observe('immediately before execution permission')
        if (not fresh(current_observation, clock(), config['observation_ttl'])
                or current_observation.get('relevant_revision') != revision
                or method == 'whole_environment' and broad_revision(current_observation, criteria) != namespace_revision):
            result['stop_reason'] = 'Incident changed or evidence expired during assessment'
            break
        if monotonic() >= deadline:
            result['stop_reason'] = 'Overall decision budget exhausted before safety gate'
            break
        try:
            gate = safety_gate(candidate, current_observation)
        except Exception as error:
            gate = {'passed': False, 'failures': ['Common gate unavailable: ' + str(error)]}
        result['common_safety_gate'] = copy.deepcopy(gate)
        valid = gate.get('passed') is True and fresh(current_observation, clock(), config['observation_ttl'])
        valid = valid and monotonic() < deadline
        try:
            if needs_replay:
                valid = valid and certificate_valid(cert, candidate_hash, current, revision,
                    clock(), config['observation_ttl'], verify_validation)
        except Exception as error:
            result['events'].append({'kind': 'permission_provenance_unavailable',
                                     'at': clock(), 'error': str(error)})
            valid = False
        result['relevant_revision'] = revision
        result['permission_observation_started_at'] = current_observation.get('started_at')
        result['full_environment_revision'] = namespace_revision
        result['permission_expires_at'] = min(current_observation.get('started_at', -math.inf) + config['observation_ttl'],
            cert['completed_at'] + config['observation_ttl'] if needs_replay else math.inf,
            clock() + max(0, deadline - monotonic()))
        result['execution_permitted'] = bool(valid)
        if valid:
            result['final_decision'] = 'Direct reuse'
            result['execution_route'] = ('Replay after full revalidation' if needs_replay
                                         else 'Direct replay of frozen candidate')
        else:
            result['stop_reason'] = 'Common gate, provenance, freshness or decision budget blocked'
        break
    else:
        result['stop_reason'] = 'Assessment count exhausted'
    if result['initial_decision'] is None:
        result['initial_decision'] = 'Stop reuse'
    result['fallback_required'] = not result['execution_permitted']
    result['completed_at'] = clock()
    return result


def permission_boundary(permission, observed, candidate_hash, current, *,
                        now=None, verify_validation=None):
    """Read-only method-specific check just before the common physical executor."""
    now = time.time() if now is None else now
    ttl = permission['configuration']['observation_ttl']
    tests = {'execution_permitted': permission.get('execution_permitted') is True,
             'candidate_matches': permission.get('candidate_sha256') == candidate_hash,
             'contract_matches': permission.get('contract_sha256') == a.digest(current),
             'fresh_permission': now <= permission.get('permission_expires_at', -math.inf),
             'fresh_observation': fresh(observed, now, ttl),
             'same_revision': observed.get('relevant_revision') == permission.get('relevant_revision')}
    if permission['method'] == 'whole_environment':
        tests['same_full_environment'] = (broad_revision(observed, permission['current_criteria'])
                                         == permission.get('full_environment_revision'))
        if permission['replays']:
            try:
                tests['valid_actual_certificate'] = certificate_valid(permission.get('validation_certificate'),
                    candidate_hash, current, observed.get('relevant_revision'), now, ttl, verify_validation)
            except Exception:
                tests['valid_actual_certificate'] = False
    return {'at': now, **tests, 'passed': all(tests.values()),
            'permission_valid_until': permission.get('permission_expires_at', -math.inf),
            'baseline_condition_states_used': False}

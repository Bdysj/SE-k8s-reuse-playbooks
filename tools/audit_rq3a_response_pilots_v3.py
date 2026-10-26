"""Read-only reconstruction of pilot decisions, permissions and physical outcomes."""
import argparse
import json
import time
from pathlib import Path

import rq3a_launch_audit_v3 as raw_audit
import comparison_policies as policies
import formal_methods as methods
import incident_observations as incidents
import reuse_assessor as a
import reuse_flow_executor as ex
import reuse_evidence as evidence
import rq3a_response_workflow as baseline
import supported_reuse_v2 as supported
from reuse_validation import Adapter


def audit_case(directory, expected=None, acceptance_override=None):
    directory = directory.resolve()  # Carried cases remain at their original immutable protocol paths.
    failures = []
    def require(value, reason):
        if not value:
            failures.append(reason)
    path = directory/'primary'
    p = ex.inputs(path); row = p['scenarios'][0]; key = row['candidate']
    acceptance_path = ex.ROOT/acceptance_override if acceptance_override else directory/'acceptance.json'
    result = json.loads(acceptance_path.read_text())
    require(result.get('passed') is True, 'outer acceptance is explicitly complete')
    if acceptance_override:
        require(result['audit_recovery']['original_acceptance_sha256'] == ex.sha(directory/'acceptance.json'),
                'preserved original acceptance identity for audit-only recovery')
        require(result['audit_recovery']['raw_live_record_sha256'] == ex.sha(path/'live-repair/record.json'),
                'unaltered physical trace for audit-only recovery')
    workflow = json.loads((path/'workflow.json').read_text())
    setup = json.loads((path/'incident-setup.json').read_text())
    observed = json.loads((path/'observations.json').read_text())
    current = a.contract(row, str((path/'protocol.json').relative_to(ex.ROOT)), ex.sha(path/'protocol.json'))
    require(result['workflow'] == workflow, 'saved workflow identity')
    require(workflow['candidate_sha256'] == p['candidate_hashes'][key], 'workflow candidate binding')
    require(workflow['contract_sha256'] == a.digest(current), 'workflow current verifier binding')
    require(workflow['configuration'] == p['decision_workflow_configuration'], 'fixed decision budgets')
    require(setup['setup_checks'][-1]['passed'] is True, 'healthy construction')
    require(all(x['target']['healthy'] and x['non_target']['passed'] for x in setup['healthy_window']),
            'healthy current-owner checks')
    require(setup['fault_confirmation'][-1]['healthy'] is False, 'actual fault confirmation')
    require(setup['fault_persistence'][-1]['elapsed_seconds'] >= 60, 'sixty-second persistent fault period')
    require(all(not x['target']['healthy'] and x['non_target']['passed'] for x in setup['fault_persistence']),
            'persistent fault with initially healthy non-targets')
    require(setup['environment_check']['passed'], 'fixed resolver profile before action')
    require('baseline confirmed' in (path/'initial-reset.log').read_text(), 'confirmed initial fixed-seed reset')
    require(workflow['started_at'] >= setup['fault_persistence'][-1]['at'], 'setup precedes decision')
    by_hash = {a.digest(x): x for x in observed}
    for event in workflow['events']:
        if event['kind'] == 'observation':
            require(event['sha256'] in by_hash, 'actual saved observation for '+event['reason'])
    first = setup['fault_persistence'][0]['target']
    incident = incidents.observed_incident(first, row['construction']['target_deployment'])
    require(json.loads((path/'permitted-incident.json').read_text())['incident'] == incident,
            'permitted incident independently extracted before any action')
    method = result['method']
    require(method == p['response_method'], 'declared method matches the actual controller')
    if workflow.get('execution_permitted'):
        permission_observation = observed[-1]
        gate = evidence.common_safety_gate(key, ex.ROOT/'candidates'/(key+'.yaml'),
            p['candidate_hashes'][key], p['context'], p['namespace'], permission_observation,
            sorted(set(row['non_target_workloads'] + [row['construction']['target_deployment']])))
        require(gate['passed'] is True and workflow['common_safety_gate']['passed'] is True,
                'independently reconstructed shared action scope and safety gate')
    criteria = methods.criteria(row, {**p, 'timing': p['criterion_timing']})
    if method == 'fault_matching':
        model = json.loads((ex.ROOT/p['comparison_model']).read_text())['model']
        recomputed = policies.fault_matching(model, key, incident)
        require(workflow['initial_decision'] == recomputed['decision'], 'independent fixed similarity decision')
        require(workflow['policy_input'] == incident and workflow['comparison_model_sha256'] == a.digest(model),
                'no fault-label/outcome or model substitution')
        require(workflow['replays'] == 0, 'fault baseline does not perform revalidation')
    elif method == 'whole_environment':
        reference = json.loads((ex.ROOT/p['whole_environment_references']).read_text())[key]
        for assessment in workflow['assessments']:
            observation = by_hash.get(assessment['observation_sha256'])
            require(observation is not None, 'whole comparison observation saved')
            if observation is not None:
                value = policies.whole_environment(reference['snapshot'], methods.scope_snapshot(observation),
                                                   reference['criteria'], criteria)
                require(assessment['decision'] == value['decision'], 'independent canonical broad comparison')
        require(workflow['comparison_reference_sha256'] == a.digest(reference), 'fixed whole comparison reference')
        require(workflow['current_criteria'] == criteria, 'same versioned recovery criteria')
        require(workflow['initial_decision'] == workflow['assessments'][0]['decision'], 'initial route retained')
    else:
        require(method == 'proposed' and workflow['history_preserved'], 'proposed method and immutable history')
        initial = workflow['assessments'][0]
        states = supported.compare(key, p['candidate_hashes'][key], by_hash[initial['observation_sha256']],
                                   current, now=initial['at'], ttl=workflow['configuration']['observation_ttl'])
        require(initial['states'] == states and workflow['initial_decision'] == a.decision(states)['decision'],
                'independent proposed initial requirement comparison')
    if method != 'proposed':
        require(workflow['condition_states_used_for_baseline_admission'] is False,
                'baseline admission independent of proposed conditions')
    certificate = workflow.get('validation_certificate')
    def verify(cert):
        request_path = directory/'replica/request.json'
        return Adapter.verify_certificate(cert, json.loads(request_path.read_text()), request_path)
    if certificate:
        try:
            require(verify(certificate), 'immutable actual validation provenance')
        except Exception as error:
            require(False, 'validation provenance: '+str(error))
        raw_replica = json.loads((directory/'replica/repair/record.json').read_text())
        require(certificate['completed_at'] <= certificate['published_at'] < raw_replica['completed_at'],
                'actual outcome time retained before later cleanup')
        require(result['replica_cleanup']['passed'], 'separate replica reset accepted')
    live_path = path/'live-repair/record.json'
    actual_executed = False
    if live_path.exists():
        live = json.loads(live_path.read_text())
        actual_executed = live.get('action_executed') is True
        audited = raw_audit.audit(live_path)
        intentional_block = result.get('launch_permission_blocked') is True
        if intentional_block:
            require(audited.get('blocked_before_action') is True,
                'explicit physical launch rejection exists')
        require(audited['accepted'],
                'independent physical outcome: '+str(audited['failures']))
        require(result['live_outcome'] == live['repair_outcome'], 'actual live outcome binding')
        require(workflow['execution_permitted'] and (actual_executed or intentional_block),
                'actual action consumes permission or is explicitly blocked at launch')
        require(live['decision_execution_boundary']['workflow_record_sha256'] == ex.sha(path/'workflow.json'),
                'physical execution consumes the same immutable permission record')
        observation = live.get('pre_action_policy_observation', live.get('launch_boundary_observation'))
        at = live.get('action_started_at', live['decision_execution_boundary']['at'])
        expiry_valid = key != 'P1' or at - setup['fault_activation_at'] + p['timing']['outcome_window'] < 1200
        if method != 'proposed':
            boundary = baseline.permission_boundary(workflow, observation, p['candidate_hashes'][key],
                                                   current, now=at, verify_validation=verify)
            require(boundary['passed'] or intentional_block and (not boundary['passed']
                or live.get('common_gate_blocked') is True or not expiry_valid),
                'candidate/revision/provenance/freshness at actual action launch')
        else:
            states = supported.compare(key, p['candidate_hashes'][key], observation, current, now=at,
                ttl=workflow['configuration']['observation_ttl'], validation=certificate)
            supported_launch = (a.decision(states, 0)['decision'] == 'Direct reuse'
                and observation['relevant_revision'] == workflow['relevant_revision']
                and at <= workflow['permission_expires_at'] and expiry_valid)
            require(supported_launch or intentional_block and (not supported_launch
                or live.get('common_gate_blocked') is True), 'supported proposed rule at launch')
        if intentional_block:
            require(live['repair_outcome'] == 'Not executed' and not live.get('action_executed'),
                    'launch block cannot be labelled executed success/failure')
        if certificate and actual_executed:
            require(0 <= at - certificate['completed_at'] <= workflow['configuration']['observation_ttl'],
                    'validation evidence fresh at actual action')
    else:
        require(not workflow['execution_permitted'] and result['live_outcome'] == 'Not executed',
                'decline is separated from an executed repair failure')
        require(result['decline_target_observation']['healthy'] is False, 'incident persists at decline')
        require('baseline confirmed' in (path/'declined-post-reset.log').read_text(), 'confirmed decline reset')
    require(result['primary_post_reset_confirmed'], 'primary reset separately accepted')
    require(result['primary_environment_after_reset']['passed'], 'primary resolver profile after reset')
    if (directory/'replica/request.json').exists():
        require(result['replica_environment_after_reset']['passed'], 'replica resolver profile after reset')
        require(result['replica_cleanup']['passed'] or result.get('controlled_uncertain_replica_reset_accepted') is True,
                'used replica has an accepted final reset boundary')
        if result.get('controlled_uncertain_replica_reset_accepted'):
            replica_record = json.loads((directory/'replica/repair/record.json').read_text())
            require(replica_record.get('post_reset_confirmed') is True
                and (directory/'replica/cancel-requested.json').exists()
                and not (directory/'replica/certificate.json').exists()
                and 'baseline confirmed' in (directory/'replica/repair/post-reset.log').read_text(),
                'independent controlled validation cancellation/reset proof')
    if expected:
        require(workflow['initial_decision'] == expected['initial_decision'], 'prospective controller route case')
        require(workflow['execution_permitted'] is expected['execution_permitted'], 'prospective permission case')
    replica_record_path = directory/'replica/repair/record.json'
    replica_record = json.loads(replica_record_path.read_text()) if replica_record_path.exists() else {}
    return {'at': time.time(), 'accepted': not failures, 'failures': failures, 'method': method,
            'case': directory.name, 'initial_decision': workflow['initial_decision'],
            'final_decision': workflow['final_decision'], 'actual_live_execution': actual_executed,
            'live_outcome': result['live_outcome'], 'replica_outcome': certificate['outcome'] if certificate else None,
            'revalidation_requests': workflow['replays'], 'observation_refreshes': workflow['refreshes'],
            'actual_isolated_execution': replica_record.get('action_executed') is True,
            'isolated_execution_outcome': replica_record.get('repair_outcome'),
            'acceptance_sha256': ex.sha(acceptance_path), 'workflow_sha256': ex.sha(path/'workflow.json'),
            'acceptance_override': acceptance_override,
            'launch_refusal_category': result.get('launch_refusal_category'),
            'launch_permission_blocked': result.get('launch_permission_blocked') is True}


def audit(out):
    plan = json.loads((out/'plan.json').read_text()); failures = []
    for name, digest in plan['source_hashes'].items():
        if ex.sha(ex.ROOT/'tools'/name) != digest or ex.sha(out/'tool-snapshot'/name) != digest:
            failures.append('Changed prospective source '+name)
    rows = []
    for case in plan['cases']:
        try:
            checked = audit_case(out/case['name'], case); rows.append(checked)
            failures += [case['name']+': '+f for f in checked['failures']]
        except Exception as error:
            failures.append(case['name']+': incomplete/unavailable audit: '+str(error))
    return {'at': time.time(), 'accepted': not failures, 'failures': failures, 'records': rows,
            'main_trials': 0, 'formal_responses_collected': 0, 'plan_sha256': ex.sha(out/'plan.json'),
            'auditor_sha256': ex.sha(Path(__file__)),
            'scope': 'Independent saved-input route and actual outcome reconstruction; no expert blinded evidence annotation.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    out = parser.parse_args().output.resolve(); answer = audit(out)
    with (out/'independent-acceptance.json').open('x') as stream:
        json.dump(answer, stream, indent=2); stream.write('\n')
    print(json.dumps(answer, indent=2))
    if not answer['accepted']:
        raise SystemExit(1)

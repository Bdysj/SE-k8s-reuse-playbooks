"""Run prospective RQ3a controller acceptance without writing formal responses."""
import argparse
import copy
import fcntl
import json
import time
import traceback
from pathlib import Path

import audit_main_development as audit
import formal_methods as methods
import incident_observations as incidents
import main_verifier as v
import registration_trials as reg
import reset_baseline as rb
import reuse_assessor as a
import reuse_evidence as evidence
import reuse_flow_executor as ex
import reuse_workflow_v2 as proposed
import rq3a_response_workflow as baselines
import supported_reuse_v2 as supported
from run_reuse_flow_development import setup, select, confirm_environment, preserve_failed_setup_logs
from reuse_validation import Adapter


def run_one(out, case):
    directory = out/case['name']; path = directory/'primary'
    p = ex.inputs(path); select(p)
    row = p['scenarios'][0]; key = row['candidate']; method = case['method']
    config = p['decision_workflow_configuration']
    current = a.contract(row, str((path/'protocol.json').relative_to(ex.ROOT)), ex.sha(path/'protocol.json'))
    result = {'type': 'rq3a_response_controller_pilot', 'case': case['name'], 'method': method,
        'scenario': row['id'], 'main_trials': 0, 'formal_registered': False, 'started_at': time.time(),
        'passed': False, 'primary_post_reset_confirmed': False, 'scientific_sources_changed': False}
    adapter = None; prepared = None; live = None
    try:
        print('START '+case['name']+' reset/healthy/fault/decision order', flush=True)
        prepared = setup(row, p, path, current)
        first = prepared['fault_persistence'][0]['target']
        incident = incidents.observed_incident(first, row['construction']['target_deployment'])
        ex.save(path/'permitted-incident.json', {'incident': incident, 'source': 'incident-setup.json',
            'selection': 'first actual fault-persistence target sample before any candidate action',
            'input_scope': 'workload, observed symptoms and ordinary error codes only'})
        adapter = Adapter(directory, p, row, prepared['injection'])
        observations = []
        def collect(candidate, contract):
            value = supported.collect(candidate, contract)
            observations.append(copy.deepcopy(value)); ex.save(path/'observations.json', observations)
            return value
        def gate(candidate, observed):
            allowed = sorted(set(row['non_target_workloads'] + [row['construction']['target_deployment']]))
            return evidence.common_safety_gate(candidate, ex.ROOT/'candidates'/(candidate+'.yaml'),
                p['candidate_hashes'][candidate], rb.CONTEXT, rb.NS, observed, allowed)
        def verify(cert):
            if adapter.request is None:
                raise RuntimeError('No actual leased replica request')
            return Adapter.verify_certificate(cert, adapter.request, directory/'replica/request.json')
        if method == 'proposed':
            history = json.loads((ex.ROOT/'candidates/registration.v1.json').read_text())
            assessed = proposed.evaluate(key, p['candidate_hashes'][key], current, history, collect, gate,
                config=config, validate=adapter.validate, compare=supported.compare)
        else:
            model = json.loads((ex.ROOT/p['comparison_model']).read_text())['model']
            reference = json.loads((ex.ROOT/p['whole_environment_references']).read_text())[key]
            criterion_protocol = {**p, 'timing': p['criterion_timing']}
            assessed = baselines.evaluate(method, key, p['candidate_hashes'][key], current, collect, gate,
                config=config, incident=incident, model=model, reference=reference,
                criteria=methods.criteria(row, criterion_protocol), validate=adapter.validate,
                verify_validation=verify)
        proposed.save_new(path/'workflow.json', assessed)
        result['workflow'] = assessed
        print(case['name']+' '+str(assessed['initial_decision'])+' -> '+str(assessed['final_decision'])+
              ' permission='+str(assessed['execution_permitted']), flush=True)
        if assessed['execution_permitted']:
            def boundary(record, observed, contract):
                if method == 'proposed':
                    states = supported.compare(key, p['candidate_hashes'][key], observed, current,
                        ttl=config['observation_ttl'], validation=assessed['validation_certificate'])
                    checks = {'current_rule_direct': a.decision(states, 0)['decision'] == 'Direct reuse',
                        'same_revision': observed['relevant_revision'] == assessed['relevant_revision'],
                        'fresh_permission': time.time() <= assessed['permission_expires_at'],
                        'same_contract': contract == current}
                    value = {'at': time.time(), **checks, 'passed': all(checks.values()), 'states': states,
                             'permission_valid_until': assessed['permission_expires_at']}
                else:
                    value = baselines.permission_boundary(assessed, observed, p['candidate_hashes'][key],
                                                          contract, verify_validation=verify)
                expiry = (key != 'P1' or time.time() - prepared['fault_activation_at']
                          + p['timing']['outcome_window'] < 1200)
                value['finished_before_fault_expiry_budget'] = expiry
                value['passed'] = value['passed'] and expiry
                value['workflow_record_sha256'] = ex.sha(path/'workflow.json')
                return value
            live = ex.run_case(row, 'repair', path/'live-repair', p, existing=prepared, before_launch=boundary)
            result['live_outcome'] = live['repair_outcome']
            result['primary_post_reset_confirmed'] = live.get('post_reset_confirmed') is True
            result['live_executor_audit'] = audit.audit(path/'live-repair/record.json')
            if not result['live_executor_audit']['accepted']:
                raise RuntimeError('Physical live trace rejected: '+str(result['live_executor_audit']['failures']))
        else:
            result['live_outcome'] = 'Not executed'
            end = v.target(row); result['decline_target_observation'] = end
            if end['healthy']:
                raise RuntimeError('Incident unexpectedly recovered without a live candidate action')
            reg.reset_s4(path/'declined-post-reset.log', p)
            result['primary_post_reset_confirmed'] = True
        result['replica_cleanup'] = adapter.finish()
        if not result['replica_cleanup']['passed']:
            raise RuntimeError('Replica trace or final reset rejected')
        result['rule_acceptance'] = (assessed['initial_decision'] == case['initial_decision']
                                    and assessed['execution_permitted'] is case['execution_permitted'])
        if method == 'whole_environment':
            cert = assessed.get('validation_certificate')
            result['complete_actual_replica_acceptance'] = bool(cert and verify(cert) and
                cert['outcome'] == ('Successful repair' if case['execution_permitted'] else 'Confirmed failure'))
        else:
            result['complete_actual_replica_acceptance'] = assessed['replays'] == 0
        result['passed'] = result['rule_acceptance'] and result['complete_actual_replica_acceptance']
    except Exception as error:
        result.update(error=str(error), traceback=traceback.format_exc())
        if prepared is None:
            try:
                preserve_failed_setup_logs(path)
            except Exception as diagnostic:
                result['setup_diagnostic_error'] = str(diagnostic)
    finally:
        # A later recovery reset never overwrites the rejected original trace.
        if not result['primary_post_reset_confirmed']:
            try:
                reg.reset_s4(path/'rejection-recovery-reset.log', p)
                result['primary_reset_recovery_confirmed'] = True
                if live is None:
                    result['primary_post_reset_confirmed'] = True
            except Exception as error:
                result['primary_reset_recovery_confirmed'] = False
                result['primary_reset_error'] = str(error)
        if adapter and adapter.child and 'replica_cleanup' not in result:
            try:
                result['replica_cleanup'] = adapter.finish()
            except Exception as error:
                result['replica_cleanup_error'] = str(error)
        try:
            result['primary_environment_after_reset'] = confirm_environment(p)
            if adapter and adapter.child:
                result['replica_environment_after_reset'] = confirm_environment(p, 'k3d-lab-validation')
            result['passed'] &= all(check['passed'] for field, check in result.items()
                                    if field.endswith('environment_after_reset'))
        except Exception as error:
            result['passed'] = False; result['environment_reset_error'] = str(error)
        result['completed_at'] = time.time()
        proposed.save_new(directory/'acceptance.json', result)
        print('FINISH '+case['name']+' passed='+str(result['passed'])+' '+str(result.get('error', '')), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case'); args = parser.parse_args(); out = args.output.resolve()
    plan = json.loads((out/'plan.json').read_text())
    for name, digest in plan['source_hashes'].items():
        assert ex.sha(ex.ROOT/'tools'/name) == digest, name
        assert ex.sha(out/'tool-snapshot'/name) == digest, name
    assert ex.sha(ex.ROOT/plan['response_plan']) == plan['response_plan_sha256']
    assert ex.sha(out/'translation-acceptance.json') == plan['translation_audit_sha256']
    with (ex.ROOT/'results/rq3a-response-incident.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, case in enumerate(plan['cases'], 1):
            if args.case and args.case != case['name']:
                continue
            receipt = out/case['name']/'acceptance.json'
            if receipt.exists():
                if not json.loads(receipt.read_text())['passed']:
                    raise RuntimeError('Preserve rejected pilot; a new prospective retry is required')
                continue
            ex.save(out/'progress.json', {'at': time.time(), 'state': 'running', 'pilot_position': index,
                'current_case': case['name'], 'pilots_completed': index - 1, 'pilots_planned': len(plan['cases']),
                'formal_responses_completed': 0})
            result = run_one(out, case)
            if not result['passed']:
                ex.save(out/'progress.json', {'at': time.time(), 'state': 'halted', 'pilot_position': index,
                    'current_case': case['name'], 'pilots_completed': index - 1, 'pilots_planned': len(plan['cases']),
                    'formal_responses_completed': 0, 'reason': result.get('error', 'controller route acceptance failed')})
                raise SystemExit(1)
        if all((out/c['name']/'acceptance.json').exists() and
               json.loads((out/c['name']/'acceptance.json').read_text())['passed'] for c in plan['cases']):
            proposed.save_new(out/'final-acceptance.json', {'at': time.time(), 'accepted': True,
                'main_trials': 0, 'formal_responses_collected': 0, 'pilots': len(plan['cases']),
                'plan_sha256': ex.sha(out/'plan.json'),
                'receipts': {c['name']: ex.sha(out/c['name']/'acceptance.json') for c in plan['cases']},
                'scope': 'Actual independent baseline controllers and proposed integration pilot; new formal source/protocol/audit freeze still required.'})
            ex.save(out/'progress.json', {'at': time.time(), 'state': 'complete',
                'pilots_completed': len(plan['cases']), 'pilots_planned': len(plan['cases']),
                'formal_responses_completed': 0})


if __name__ == '__main__':
    main()

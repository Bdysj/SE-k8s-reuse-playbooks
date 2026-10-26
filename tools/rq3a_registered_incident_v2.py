"""Actual method-controlled incident component for separately registered RQ3a responses.

Lower-level physical executor traces retain their original codec and zero RQ1
counterfactual count. This outer response record alone counts one RQ3a incident.
No prospective case outcome or expected route enters the formal decision rule."""
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
from rq3a_incident_reset_v2 import reset_incident
import reset_baseline as rb
import reuse_assessor as a
import reuse_evidence as evidence
import reuse_flow_executor as ex
import reuse_workflow_v2 as proposed
import rq3a_response_workflow as baselines
import supported_reuse_v2 as supported
from run_reuse_flow_development import setup, select, confirm_environment, preserve_failed_setup_logs
from reuse_validation import Adapter


def validation_acceptance(assessed, cleanup, verify, controlled=False):
    if assessed['replays'] == 0:
        return True
    certificate = assessed.get('validation_certificate')
    if certificate and verify(certificate) and certificate['outcome'] in (
            'Successful repair', 'Confirmed failure'):
        return True
    return (not assessed['execution_permitted']
            and (cleanup['passed'] or controlled))


def run_one(out, case):
    directory = out/case['name']; path = directory/'primary'
    p = ex.inputs(path); select(p)
    row = p['scenarios'][0]; key = row['candidate']; method = case['method']
    config = p['decision_workflow_configuration']
    current = a.contract(row, str((path/'protocol.json').relative_to(ex.ROOT)), ex.sha(path/'protocol.json'))
    result = {'type': 'formal_rq3a_method_response', 'case': case['name'], 'method': method,
        'scenario': row['id'], 'main_trials': 0, 'formal_registered': True, 'response_trials': 1, 'trial_identity': copy.deepcopy(p['trial_identity']), 'started_at': time.time(),
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
                record['launch_boundary_observation'] = copy.deepcopy(observed)
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
                intentional_block = (not live.get('action_executed') and live.get('post_reset_confirmed') is True
                    and (live.get('decision_execution_boundary', {}).get('passed') is False
                         or live.get('common_gate_blocked') is True))
                if not intentional_block:
                    raise RuntimeError('Physical live trace rejected: '+str(result['live_executor_audit']['failures']))
                result['launch_permission_blocked'] = True
        else:
            result['live_outcome'] = 'Not executed'
            end = v.target(row); result['decline_target_observation'] = end
            if end['healthy']:
                raise RuntimeError('Incident unexpectedly recovered without a live candidate action')
            reset_incident(row, p, path/'declined-post-reset.log')
            result['primary_post_reset_confirmed'] = True
        result['replica_cleanup'] = adapter.finish()
        if not result['replica_cleanup']['passed']:
            rr_path = directory/'replica/repair/record.json'
            rr = json.loads(rr_path.read_text()) if rr_path.exists() else {}
            controlled = (rr.get('post_reset_confirmed') is True
                and (directory/'replica/cancel-requested.json').exists()
                and not (directory/'replica/certificate.json').exists()
                and 'baseline confirmed' in (directory/'replica/repair/post-reset.log').read_text())
            result['controlled_uncertain_replica_reset_accepted'] = controlled
            result['uncertain_isolated_outcome'] = rr.get('repair_outcome')
            if not controlled:
                raise RuntimeError('Replica trace or final reset rejected')
        result['rule_acceptance'] = True  # Reconstructed by the separate formal auditor; no expected outcome/route label.
        result['complete_actual_replica_acceptance'] = validation_acceptance(
            assessed, result['replica_cleanup'], verify,
            result.get('controlled_uncertain_replica_reset_accepted') is True)
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
                reset_incident(row, p, path/'rejection-recovery-reset.log')
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

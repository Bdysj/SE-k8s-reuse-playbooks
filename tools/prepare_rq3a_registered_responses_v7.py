"""Unchanged scientific queue after an explicit pre-decision setup rejection."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import time

import reuse_flow_executor as ex
import prepare_rq3a_registered_responses as original
from audit_rq3a_response_pilots_v3 import audit_case
from run_rq3a_registered_responses_v4 import check

PREVIOUS = ex.ROOT / 'results/formal-rq3a-responses-v6'
RECOVERY = PREVIOUS / 'recoveries/healthy-145-v7'
CARRIED = 144
IDENTITY_FIELDS = ('position', 'scenario', 'candidate', 'repeat', 'method', 'matched_block', 'trial_id', 'name')

def validate_rejection(status, rejection, result, setup, *, live_record_exists, workflow_exists, replica_record_exists):
    if not (status['state'] == 'halted' and status['position'] == 145
            and status['responses_completed'] == CARRIED):
        raise ValueError('Continuation requires exactly144 accepted responses and halted145')
    trial = rejection['trial_identity']
    if not (trial['position'] == 145 and trial['scenario'] == 'P2-2-1' and trial['candidate'] == 'P2'
            and trial['repeat'] == 2 and trial['method'] == 'fault_matching'):
        raise ValueError('Interrupted response identity mismatch')
    if any(result['trial_identity'][key] != trial[key] for key in IDENTITY_FIELDS[:-1]):
        raise ValueError('Saved rejection must bind the same physical trial identity')
    if (rejection['candidate_action_executed'] is not False or rejection['live_outcome'] != 'Not executed'
            or live_record_exists or workflow_exists or replica_record_exists):
        raise ValueError('Pre-fault continuation cannot hide a decision or physical execution')
    if not (result['passed'] is False and result['primary_post_reset_confirmed'] is True
            and result['primary_reset_recovery_confirmed'] is True and result['error'] == 'Healthy gate rejected'):
        raise ValueError('Original incomplete healthy setup and confirmed recovery required')
    if setup.get('setup_phase') != 'healthy observation window' or any(
            name in setup for name in ('fault_activation_at','injection','fault_confirmation','fault_persistence')):
        raise ValueError('Only pre-fault healthy-window rejection is eligible for this continuation')
    samples = setup['healthy_window']
    if not samples or any(x['non_target']['passed'] is not True for x in samples):
        raise ValueError('Do not hide a non-target failure as target latency')
    target = samples[-1]['target']
    count = target.get('json', {}).get('rating_count')
    if not (target['healthy'] is False and target['status'] == 200 and target['curl_rc'] == 0
            and target['seconds'] > 1.0 and target['structured_count_valid'] is True
            and isinstance(count,int) and not isinstance(count,bool) and count >= 0
            and target['workload']['healthy'] is True and target['workload']['desired'] == 3
            and target['endpoint_binding']['healthy'] is True):
        raise ValueError('Expected saved pre-fault valid ratings response beyond unchanged1s criterion')

def same_scientific_component(new, old):
    for key in ('timing', 'criterion_timing', 'decision_workflow_configuration',
                'candidate_hashes', 'environment_profile', 'context', 'namespace'):
        if new[key] != old[key]:
            raise ValueError('Scientific component changed: ' + key)
    if len(new['scenarios']) != len(old['scenarios']) or len(new['scenarios']) != 1:
        raise ValueError('One unchanged registered scenario required')
    # Relocated paths and their document identities are operational metadata.
    for key, value in old['scenarios'][0].items():
        if key not in ('healthy_manifest', 'manifest_sha256') and new['scenarios'][0].get(key) != value:
            raise ValueError('Frozen scenario construction changed: ' + key)

def prepare(out):
    previous = check(PREVIOUS)
    status = json.loads((PREVIOUS / 'progress.json').read_text())
    rejected = json.loads((PREVIOUS / 'rejected-attempts.json').read_text())
    interrupted = PREVIOUS / previous['queue'][CARRIED]['name']
    result = json.loads((interrupted / 'acceptance.json').read_text())
    setup = json.loads((interrupted / 'primary/incident-setup.json').read_text())
    validate_rejection(status, rejected[-1], result, setup,
        live_record_exists=(interrupted/'primary/live-repair/record.json').exists(),
        workflow_exists=(interrupted/'primary/workflow.json').exists(),
        replica_record_exists=(interrupted/'replica/repair/record.json').exists())
    recovery = json.loads((RECOVERY / 'recovery-acceptance.json').read_text())
    assert recovery['accepted'] and len(recovery['proofs']) == 2
    assert all(x['baseline_confirmed'] and x['environment_confirmed'] for x in recovery['proofs'])
    for proof in recovery['proofs']:
        role = proof['role']
        for name, field in [('recovery-reset.log','reset_log_sha256'),
                            ('recovery-reset.cleanup.json','cleanup_sha256'),
                            ('recovery-environment.json','environment_sha256')]:
            assert ex.sha(RECOVERY/(role+'-'+name)) == proof[field]
    assert ex.sha(RECOVERY/'restore.py') == recovery['recovery_source_sha256']
    preserved = json.loads((RECOVERY / 'preserved-identities.json').read_text())
    for name, digest in preserved['identities'].items():
        assert ex.sha(ex.ROOT / name) == digest, name
    units = json.loads((RECOVERY / 'unit-acceptance.json').read_text())
    assert units['accepted']
    for name, digest in units['implementation_sources'].items():
        assert ex.sha(ex.ROOT/name) == digest, name
    checked = []
    for trial in previous['queue'][:CARRIED]:
        record = audit_case(PREVIOUS / trial['name'], acceptance_override=trial.get('acceptance_override'))
        assert record['accepted'], record['failures']
        checked.append(record)
    original.prepare(out, ex.ROOT / previous['pilot_directory'])
    protocol = json.loads((out / 'protocol.json').read_text())
    for trial, old in zip(protocol['queue'], previous['queue']):
        assert all(trial[k] == old[k] for k in IDENTITY_FIELDS)
        directory = out / trial['name']
        for role in ('primary', 'replica'):
            for name in ('baseline.yaml', 'baseline.fingerprint.json', 'healthy.yaml'):
                assert ex.sha(directory/role/name) == ex.sha(PREVIOUS/old['name']/role/name)
            new = json.loads((directory/role/'protocol.json').read_text())
            prior = json.loads((PREVIOUS/old['name']/role/'protocol.json').read_text())
            same_scientific_component(new, prior)
        if trial['position'] <= CARRIED:
            assert not (directory / 'acceptance.json').exists()
            shutil.rmtree(directory)  # Only the just-created, never-run definition.
            directory.symlink_to(os.path.relpath(PREVIOUS/trial['name'], out), target_is_directory=True)
            trial.update(protocol_sha256=copy.deepcopy(old['protocol_sha256']),
                carried_from_directory=str((PREVIOUS/trial['name']).relative_to(ex.ROOT)),
                carried_acceptance_sha256=ex.sha(directory/'acceptance.json'))
            if old.get('acceptance_override'):
                for field in ('acceptance_override', 'acceptance_override_sha256', 'audit_only_recovery'):
                    trial[field] = old[field]
                dest = out/'audit-overrides'/trial['name']; dest.mkdir(parents=True)
                ex.save(dest/'independent-audit.json', checked[trial['position']-1])
        else:
            for role in ('primary', 'replica'):
                child = directory/role
                component = json.loads((child/'protocol.json').read_text())
                component.update(version='formal-rq3a-response-v7-'+trial['name']+'-'+role,
                    operational_amendment=str((out/'amendment.json').relative_to(ex.ROOT)))
                ex.save(child/'protocol.json', component)
                frozen = json.loads((child/'executor-freeze.json').read_text())
                frozen['protocol_sha256'] = ex.sha(child/'protocol.json')
                ex.save(child/'executor-freeze.json', frozen)
                trial['protocol_sha256'][role] = frozen['protocol_sha256']
    acceptance = {'at':time.time(),'accepted':True,'response_identities_checked':240,
        'role_manifest_pairs_checked':480,'accepted_responses_reconstructed':CARRIED,
        'original_interrupted_attempt_identity_count':len(preserved['identities']),
        'original145_remains_separate_not_executed_setup_rejection':True,
        'scientific_components_unchanged':True}
    ex.save(RECOVERY/'continuation-acceptance.json', acceptance)
    amendment = {'at':time.time(),
        'scope':'Strictly restore both environments, then execute a separate fresh145 after a saved pre-fault '
                'ratings response beyond the frozen1s health bound. Keep all original raw evidence and144 accepted responses. No code path, '
                'threshold, retry rule, observer deadline, health predicate, decision, fault or workload change.',
        'previous_protocol':str((PREVIOUS/'protocol.json').relative_to(ex.ROOT)),
        'previous_protocol_sha256':ex.sha(PREVIOUS/'protocol.json'),
        'preserved_rejected_attempts':str((PREVIOUS/'rejected-attempts.json').relative_to(ex.ROOT)),
        'preserved_rejected_attempts_sha256':ex.sha(PREVIOUS/'rejected-attempts.json'),
        'accepted_responses_carried_once':CARRIED,'fresh_execution_starts_at':145,
        'response39_audit_only_overlay_preserved':True,
        'original145_initial_decision_reached':False,
        'original145_candidate_action_executed':False,
        'carried_independent_reconstruction':checked}
    for field, name in [('recovery_acceptance','recovery-acceptance.json'),
                        ('unit_acceptance','unit-acceptance.json'),
                        ('continuation_acceptance','continuation-acceptance.json')]:
        amendment[field] = str((RECOVERY/name).relative_to(ex.ROOT))
        amendment[field+'_sha256'] = ex.sha(RECOVERY/name)
    ex.save(out/'amendment.json', amendment)
    protocol.update(version='formal-rq3a-response-v7',registered_at=time.time(),
        operational_amendment=str((out/'amendment.json').relative_to(ex.ROOT)),
        operational_amendment_sha256=ex.sha(out/'amendment.json'),
        previous_response_protocol=amendment['previous_protocol'],
        previous_response_protocol_sha256=amendment['previous_protocol_sha256'],
        carried_responses=CARRIED,fresh_response_slots=240-CARRIED,
        incident_executor=previous['incident_executor'],response_runner=previous['response_runner'],
        response_auditor=previous['response_auditor'],normal_refusal_policy=previous['normal_refusal_policy'])
    ex.save(out/'protocol.json', protocol)
    ex.save(out/'freeze.json', {'at':time.time(),'protocol_sha256':ex.sha(out/'protocol.json'),
        'source_hashes':protocol['source_hashes'],'amendment_sha256':ex.sha(out/'amendment.json'),'immutable':True})
    ex.save(out/'rejected-attempts.json', rejected)
    shutil.copyfile(PREVIOUS/'audit-only-recovery-denominator.json',out/'audit-only-recovery-denominator.json')
    ex.save(out/'progress.json', {'state':'registered','at':time.time(),'position':CARRIED,
        'responses_completed':CARRIED,'responses_planned':240,'formal_registered':True})
    check(out)
    print('Registered unchanged queue:144 carried once; separately recorded fresh145.', flush=True)

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    prepare(parser.parse_args().output.resolve())

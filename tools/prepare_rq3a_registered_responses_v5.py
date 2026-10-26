"""Prospective saved-timer correction with a separate fresh response92; preserve all evidence."""
import argparse
import copy
import json
import os
import shutil
import time
from pathlib import Path

import reuse_flow_executor as ex
import prepare_rq3a_registered_responses as original
from audit_rq3a_response_pilots_v3 import audit_case
from run_rq3a_registered_responses_v3 import check as previous_check
from run_rq3a_registered_responses_v4 import check

PREVIOUS = ex.ROOT / 'results/formal-rq3a-responses-v4'
RECOVERY = PREVIOUS / 'recoveries/persistence-092-v5'
CARRIED = 91


def prepare(out):
    previous = previous_check(PREVIOUS)
    status = json.loads((PREVIOUS / 'progress.json').read_text())
    assert status['state'] == 'halted' and status['position'] == 92
    assert status['responses_completed'] == CARRIED
    recovery = json.loads((RECOVERY / 'recovery-acceptance.json').read_text())
    assert recovery['accepted'] and len(recovery['proofs']) == 2
    preserved = json.loads((RECOVERY / 'preserved-identities.json').read_text())
    for name, digest in preserved['identities'].items():
        assert ex.sha(ex.ROOT / name) == digest, name
    rejected = json.loads((PREVIOUS / 'rejected-attempts.json').read_text())
    assert rejected[-1]['trial_identity']['position'] == 92
    assert rejected[-1]['candidate_action_executed'] is True
    assert rejected[-1]['live_outcome'] == 'Successful repair'
    assert rejected[-1]['audit']['failures'] == ['sixty-second persistent fault period']
    units = json.loads((RECOVERY / 'unit-acceptance.json').read_text())
    assert units['accepted']
    checked = []
    for trial in previous['queue'][:CARRIED]:
        record = audit_case(PREVIOUS / trial['name'],
                            acceptance_override=trial.get('acceptance_override'))
        assert record['accepted'], record['failures']
        checked.append(record)
    original.prepare(out, ex.ROOT / previous['pilot_directory'])
    protocol = json.loads((out / 'protocol.json').read_text())
    for trial, old in zip(protocol['queue'], previous['queue']):
        assert all(trial[k] == old[k] for k in ('position', 'scenario', 'candidate',
                   'repeat', 'method', 'matched_block', 'trial_id', 'name'))
        directory = out / trial['name']
        for role in ('primary', 'replica'):
            for name in ('baseline.yaml', 'baseline.fingerprint.json', 'healthy.yaml'):
                assert ex.sha(directory / role / name) == ex.sha(PREVIOUS / old['name'] / role / name)
            generated = json.loads((directory / role / 'protocol.json').read_text())
            prior = json.loads((PREVIOUS / old['name'] / role / 'protocol.json').read_text())
            assert all(generated[k] == prior[k] for k in ('timing', 'criterion_timing',
                'decision_workflow_configuration', 'candidate_hashes', 'environment_profile'))
        if trial['position'] <= CARRIED:
            assert not (directory / 'acceptance.json').exists()
            shutil.rmtree(directory)  # Newly generated unused definitions only.
            directory.symlink_to(os.path.relpath(PREVIOUS / trial['name'], out), target_is_directory=True)
            trial.update(protocol_sha256=copy.deepcopy(old['protocol_sha256']),
                carried_from_directory=str((PREVIOUS / trial['name']).relative_to(ex.ROOT)),
                carried_acceptance_sha256=ex.sha(directory / 'acceptance.json'))
            if old.get('acceptance_override'):
                for field in ('acceptance_override', 'acceptance_override_sha256', 'audit_only_recovery'):
                    trial[field] = old[field]
                dest = out / 'audit-overrides' / trial['name']; dest.mkdir(parents=True)
                ex.save(dest / 'independent-audit.json', checked[trial['position'] - 1])
        else:
            for role in ('primary', 'replica'):
                child = directory / role
                p = json.loads((child / 'protocol.json').read_text())
                p['version'] = 'formal-rq3a-response-v5-' + trial['name'] + '-' + role
                p['operational_amendment'] = str((out / 'amendment.json').relative_to(ex.ROOT))
                ex.save(child / 'protocol.json', p)
                frozen = json.loads((child / 'executor-freeze.json').read_text())
                frozen['protocol_sha256'] = ex.sha(child / 'protocol.json')
                ex.save(child / 'executor-freeze.json', frozen)
                trial['protocol_sha256'][role] = frozen['protocol_sha256']
    acceptance = {'at': time.time(), 'accepted': True, 'response_identities_checked': 240,
        'role_manifest_pairs_checked': 480, 'accepted_responses_reconstructed': CARRIED,
        'original_interrupted_attempt_identity_count': len(preserved['identities']),
        'original_executed_success_with_incomplete_setup_not_accepted': True,
        'physical_executor_auditor_decisions_and_windows_unchanged': True,
        'saved_persistence_elapsed_controls_termination': True}
    ex.save(RECOVERY / 'continuation-acceptance.json', acceptance)
    amendment = {'at': time.time(),
        'scope': 'Fresh separately recorded response92 after a sub60s saved persistence sample. '
                 'Termination now tests the saved elapsed time, requiring an extra sample if necessary. '
                 'No threshold, observer deadline, policy, fault, workload or decision rule changes.',
        'previous_protocol': str((PREVIOUS / 'protocol.json').relative_to(ex.ROOT)),
        'previous_protocol_sha256': ex.sha(PREVIOUS / 'protocol.json'),
        'preserved_rejected_attempts': str((PREVIOUS / 'rejected-attempts.json').relative_to(ex.ROOT)),
        'preserved_rejected_attempts_sha256': ex.sha(PREVIOUS / 'rejected-attempts.json'),
        'recovery_acceptance': str((RECOVERY / 'recovery-acceptance.json').relative_to(ex.ROOT)),
        'recovery_acceptance_sha256': ex.sha(RECOVERY / 'recovery-acceptance.json'),
        'unit_acceptance': str((RECOVERY / 'unit-acceptance.json').relative_to(ex.ROOT)),
        'unit_acceptance_sha256': ex.sha(RECOVERY / 'unit-acceptance.json'),
        'continuation_acceptance': str((RECOVERY / 'continuation-acceptance.json').relative_to(ex.ROOT)),
        'continuation_acceptance_sha256': ex.sha(RECOVERY / 'continuation-acceptance.json'),
        'accepted_responses_carried_once': CARRIED, 'fresh_execution_starts_at': 92,
        'response39_audit_only_overlay_preserved': True,
        'original_response92_remains_separate_executed_setup_rejection': True,
        'carried_independent_reconstruction': checked}
    ex.save(out / 'amendment.json', amendment)
    protocol.update(version='formal-rq3a-response-v5', registered_at=time.time(),
        operational_amendment=str((out / 'amendment.json').relative_to(ex.ROOT)),
        operational_amendment_sha256=ex.sha(out / 'amendment.json'),
        previous_response_protocol=amendment['previous_protocol'],
        previous_response_protocol_sha256=amendment['previous_protocol_sha256'],
        carried_responses=CARRIED, fresh_response_slots=240 - CARRIED,
        incident_executor='rq3a_registered_incident_v4.py', response_runner='run_rq3a_registered_responses_v4.py',
        response_auditor=previous['response_auditor'],
        normal_refusal_policy=previous['normal_refusal_policy'])
    ex.save(out / 'protocol.json', protocol)
    ex.save(out / 'freeze.json', {'at': time.time(), 'protocol_sha256': ex.sha(out / 'protocol.json'),
        'source_hashes': protocol['source_hashes'], 'amendment_sha256': ex.sha(out / 'amendment.json'),
        'immutable': True})
    ex.save(out / 'rejected-attempts.json', rejected)
    shutil.copyfile(PREVIOUS / 'audit-only-recovery-denominator.json',
                    out / 'audit-only-recovery-denominator.json')
    ex.save(out / 'progress.json', {'state': 'registered', 'at': time.time(), 'position': CARRIED,
        'responses_completed': CARRIED, 'responses_planned': 240, 'formal_registered': True})
    check(out)
    print('Registered unchanged queue:91 carried once; fresh92; short-window attempt retained.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output.resolve())

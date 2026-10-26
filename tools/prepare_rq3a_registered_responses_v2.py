"""Prospective operational amendment; carry accepted responses once, retry4 afresh."""
import argparse
import json
import os
import shutil
import time
from pathlib import Path

import reuse_flow_executor as ex
import prepare_rq3a_registered_responses as original
from audit_rq3a_response_pilots import audit_case
from run_rq3a_registered_responses import check as check_previous

PREVIOUS = ex.ROOT/'results/formal-rq3a-responses-v1'
RECOVERY = PREVIOUS/'recoveries/cleanup-scope-004-v2'


def prepare(out):
    previous = check_previous(PREVIOUS)
    assert json.loads((PREVIOUS/'progress.json').read_text())['responses_completed'] == 3
    units = json.loads((RECOVERY/'unit-acceptance.json').read_text())
    assert units['accepted'] and units['cleanup_tests_passed'] == 8 and units['evidence_tests_passed'] == 5
    for name, digest in units['source_hashes'].items():
        assert ex.sha(ex.ROOT/'tools'/name) == digest, name
    assert json.loads((RECOVERY/'recovery-acceptance.json').read_text())['accepted']
    checked = []
    for trial in previous['queue'][:3]:
        directory = PREVIOUS/trial['name']
        record = audit_case(directory)
        prior = json.loads((directory/'independent-audit.json').read_text())
        assert record['accepted'] and record['acceptance_sha256'] == prior['acceptance_sha256']
        checked.append(record)
    original.prepare(out, ex.ROOT/previous['pilot_directory'])
    protocol = json.loads((out/'protocol.json').read_text())
    before_carry = out/'generated-protocol-before-carry.json'
    before_carry.write_bytes((out/'protocol.json').read_bytes())
    for trial, old_trial in zip(protocol['queue'], previous['queue']):
        assert all(trial[k] == old_trial[k] for k in
            ('position', 'scenario', 'candidate', 'repeat', 'method', 'matched_block', 'trial_id', 'name'))
        directory = out/trial['name']
        if trial['position'] <= 3:
            assert not (directory/'acceptance.json').exists()
            shutil.rmtree(directory)  # Unused generated definitions only, never physical data.
            directory.symlink_to(os.path.relpath(PREVIOUS/trial['name'], out), target_is_directory=True)
            trial['protocol_sha256'] = old_trial['protocol_sha256']
            trial['carried_from_directory'] = str((PREVIOUS/trial['name']).relative_to(ex.ROOT))
            trial['carried_acceptance_sha256'] = ex.sha(directory/'acceptance.json')
        else:
            for role in ('primary', 'replica'):
                child = directory/role
                p = json.loads((child/'protocol.json').read_text())
                p['version'] = 'formal-rq3a-response-v2-'+trial['name']+'-'+role
                p['operational_amendment'] = str((out/'amendment.json').relative_to(ex.ROOT))
                ex.save(child/'protocol.json', p)
                frozen = json.loads((child/'executor-freeze.json').read_text())
                frozen['protocol_sha256'] = ex.sha(child/'protocol.json')
                ex.save(child/'executor-freeze.json', frozen)
                trial['protocol_sha256'][role] = frozen['protocol_sha256']
    amendment = {'at': time.time(), 'scope': 'Post-decision logical-target cleanup for renamed targets; '
        'complete registered evidence acceptance for zero-replay and actual revalidation routes of every method.',
        'previous_protocol': str((PREVIOUS/'protocol.json').relative_to(ex.ROOT)),
        'previous_protocol_sha256': ex.sha(PREVIOUS/'protocol.json'),
        'previous_freeze_sha256': ex.sha(PREVIOUS/'freeze.json'),
        'preserved_rejected_attempts': str((PREVIOUS/'rejected-attempts.json').relative_to(ex.ROOT)),
        'preserved_rejected_attempts_sha256': ex.sha(PREVIOUS/'rejected-attempts.json'),
        'recovery_acceptance': str((RECOVERY/'recovery-acceptance.json').relative_to(ex.ROOT)),
        'recovery_acceptance_sha256': ex.sha(RECOVERY/'recovery-acceptance.json'),
        'unit_acceptance': str((RECOVERY/'unit-acceptance.json').relative_to(ex.ROOT)),
        'unit_acceptance_sha256': ex.sha(RECOVERY/'unit-acceptance.json'),
        'accepted_responses_carried_once': 3, 'carried_independent_reconstruction': checked,
        'fresh_retry_starts_at': 4, 'decision_rules_candidates_and_timing_unchanged': True,
        'original87_scientific_sources_unchanged': True,
        'prior_rejected_response_and_isolated_action_not_erased': True,
        'queue_order_scenarios_policies_repeats_unchanged': True,
        'no_new_data_collected_before_this_amendment': True}
    ex.save(out/'amendment.json', amendment)
    protocol.update(version='formal-rq3a-response-v2', registered_at=time.time(),
        operational_amendment=str((out/'amendment.json').relative_to(ex.ROOT)),
        operational_amendment_sha256=ex.sha(out/'amendment.json'),
        previous_response_protocol=amendment['previous_protocol'],
        previous_response_protocol_sha256=amendment['previous_protocol_sha256'],
        carried_responses=3, fresh_response_slots=237,
        incident_executor='rq3a_registered_incident_v2.py',
        response_runner='run_rq3a_registered_responses_v2.py')
    ex.save(out/'protocol.json', protocol)
    ex.save(out/'freeze.json', {'at': time.time(), 'protocol_sha256': ex.sha(out/'protocol.json'),
        'source_hashes': protocol['source_hashes'], 'amendment_sha256': ex.sha(out/'amendment.json'), 'immutable': True})
    ex.save(out/'rejected-attempts.json', json.loads((PREVIOUS/'rejected-attempts.json').read_text()))
    ex.save(out/'progress.json', {'state': 'registered', 'at': time.time(), 'position': 3,
        'responses_completed': 3, 'responses_planned': 240, 'formal_registered': True,
        'retained_rejected_attempts': 1, 'carried_responses_counted_once': 3})
    print('Registered operational amendment: three unchanged responses retained once; fresh retry begins at4.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output.resolve())

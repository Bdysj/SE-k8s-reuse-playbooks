"""Freeze refusal-aware audit continuation;38 preserved records plus audit-only39."""
import argparse
import json
import os
import shutil
import time
from pathlib import Path

import reuse_flow_executor as ex
import prepare_rq3a_registered_responses as original
from audit_rq3a_response_pilots_v3 import audit_case
from run_rq3a_registered_responses_v2 import check as check_previous

PREVIOUS = ex.ROOT/'results/formal-rq3a-responses-v2'
RECOVERY = PREVIOUS/'recoveries/launch-refusal-039-v3'


def prepare(out):
    previous = check_previous(PREVIOUS)
    assert json.loads((PREVIOUS/'progress.json').read_text())['responses_completed'] == 38
    audit_recovery = json.loads((RECOVERY/'recovery-acceptance.json').read_text())
    assert audit_recovery['accepted'] and audit_recovery['audited_responses'] == 39
    units = json.loads((RECOVERY/'unit-acceptance.json').read_text())
    assert units['accepted'] and units['new_refusal_tests_passed'] == 10
    for name, digest in units['source_hashes'].items():
        assert ex.sha(ex.ROOT/'tools'/name) == digest, name
    override = str((RECOVERY/'response39-supplemental-acceptance.json').relative_to(ex.ROOT))
    checked = []
    for trial in previous['queue'][:39]:
        directory = PREVIOUS/trial['name']
        supplement = override if trial['position'] == 39 else None
        record = audit_case(directory, acceptance_override=supplement)
        assert record['accepted'], record['failures']
        if not supplement:
            old = json.loads((directory/'independent-audit.json').read_text())
            assert old['accepted'] and old['acceptance_sha256'] == record['acceptance_sha256']
        checked.append(record)
    original.prepare(out, ex.ROOT/previous['pilot_directory'])
    protocol = json.loads((out/'protocol.json').read_text())
    (out/'generated-protocol-before-carry.json').write_bytes((out/'protocol.json').read_bytes())
    for trial, old_trial in zip(protocol['queue'], previous['queue']):
        assert all(trial[k] == old_trial[k] for k in
            ('position', 'scenario', 'candidate', 'repeat', 'method', 'matched_block', 'trial_id', 'name'))
        directory = out/trial['name']
        if trial['position'] <= 39:
            assert not (directory/'acceptance.json').exists()
            shutil.rmtree(directory)  # Generated, never-run definitions only.
            directory.symlink_to(os.path.relpath(PREVIOUS/trial['name'], out), target_is_directory=True)
            trial.update(protocol_sha256=old_trial['protocol_sha256'],
                carried_from_directory=str((PREVIOUS/trial['name']).relative_to(ex.ROOT)),
                carried_acceptance_sha256=ex.sha(directory/'acceptance.json'))
            if trial['position'] == 39:
                trial.update(acceptance_override=override,
                    acceptance_override_sha256=ex.sha(ex.ROOT/override), audit_only_recovery=True)
                path = out/'audit-overrides'/trial['name']; path.mkdir(parents=True)
                ex.save(path/'independent-audit.json', checked[-1])
        else:
            for role in ('primary', 'replica'):
                child = directory/role
                p = json.loads((child/'protocol.json').read_text())
                p['version'] = 'formal-rq3a-response-v3-'+trial['name']+'-'+role
                p['operational_amendment'] = str((out/'amendment.json').relative_to(ex.ROOT))
                ex.save(child/'protocol.json', p)
                frozen = json.loads((child/'executor-freeze.json').read_text())
                frozen['protocol_sha256'] = ex.sha(child/'protocol.json')
                ex.save(child/'executor-freeze.json', frozen)
                trial['protocol_sha256'][role] = frozen['protocol_sha256']
    amendment = {'at': time.time(),
        'scope': 'Audit-only schema correction: proven pre-launch refusals are Not executed and may '
                 'continue after confirmed reset. Executed/incomplete/unknown traces still use strict acceptance.',
        'previous_protocol': str((PREVIOUS/'protocol.json').relative_to(ex.ROOT)),
        'previous_protocol_sha256': ex.sha(PREVIOUS/'protocol.json'),
        'preserved_rejected_attempts': str((PREVIOUS/'rejected-attempts.json').relative_to(ex.ROOT)),
        'preserved_rejected_attempts_sha256': ex.sha(PREVIOUS/'rejected-attempts.json'),
        'recovery_acceptance': str((RECOVERY/'recovery-acceptance.json').relative_to(ex.ROOT)),
        'recovery_acceptance_sha256': ex.sha(RECOVERY/'recovery-acceptance.json'),
        'unit_acceptance': str((RECOVERY/'unit-acceptance.json').relative_to(ex.ROOT)),
        'unit_acceptance_sha256': ex.sha(RECOVERY/'unit-acceptance.json'),
        'accepted_responses_preserved': 38, 'existing_refusal_response_independently_recovered': 39,
        'responses_carried_once': 39, 'carried_independent_reconstruction': checked,
        'fresh_execution_starts_at': 40, 'response39_replayed': False,
        'decision_rules_candidates_timing_and_physical_executor_unchanged': True,
        'original87_scientific_sources_unchanged': True,
        'old_rejections_and_original_failed_wrapper_metadata_preserved': True,
        'queue_order_scenarios_policies_repeats_unchanged': True}
    ex.save(out/'amendment.json', amendment)
    protocol.update(version='formal-rq3a-response-v3', registered_at=time.time(),
        operational_amendment=str((out/'amendment.json').relative_to(ex.ROOT)),
        operational_amendment_sha256=ex.sha(out/'amendment.json'),
        previous_response_protocol=amendment['previous_protocol'],
        previous_response_protocol_sha256=amendment['previous_protocol_sha256'],
        carried_responses=39, fresh_response_slots=201,
        incident_executor='rq3a_registered_incident_v3.py',
        response_runner='run_rq3a_registered_responses_v3.py',
        response_auditor='audit_rq3a_response_pilots_v3.py',
        normal_refusal_policy='Explicit fixed-gate refusal with no action/runner evidence, exact '
            'candidate/protocol provenance and separately confirmed resets is Not executed. '
            'It is never a repair success or an executed repair failure. Unknown errors, '
            'unconfirmed resets, missing action evidence and incomplete executed windows halt.')
    ex.save(out/'protocol.json', protocol)
    ex.save(out/'freeze.json', {'at': time.time(), 'protocol_sha256': ex.sha(out/'protocol.json'),
        'source_hashes': protocol['source_hashes'], 'amendment_sha256': ex.sha(out/'amendment.json'), 'immutable': True})
    ex.save(out/'rejected-attempts.json', json.loads((PREVIOUS/'rejected-attempts.json').read_text()))
    ex.save(out/'progress.json', {'state': 'registered', 'at': time.time(), 'position': 39,
        'responses_completed': 39, 'responses_planned': 240, 'formal_registered': True,
        'retained_rejected_attempts': 2, 'audit_only_recovered_refusals': 1})
    print('Registered refusal-aware continuation:38 unchanged responses plus the preserved39th '
          'Not executed response audited without replay; next execution40.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output.resolve())

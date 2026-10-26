"""Retry post-decision reset acceptance; preserve every earlier physical trace."""
import argparse
import json
import os
import shutil
import time
from pathlib import Path

import prepare_rq3a_response_pilots as prior
import reuse_flow_executor as ex
from audit_rq3a_response_pilots import audit_case


def prepare(out):
    parent = ex.ROOT/'results/RQ3a-controller-pilots-v2'
    old = json.loads((parent/'plan.json').read_text())
    rejected_path = parent/'04-whole-decline-P5/acceptance.json'
    rejected = json.loads(rejected_path.read_text())
    assert rejected['passed'] is False
    assert rejected['workflow']['final_decision'] == 'Stop reuse'
    assert rejected['live_outcome'] == 'Not executed'
    assert rejected['replica_cleanup']['passed']
    assert 'Baseline reset failed' in rejected['error']
    recovery = ex.ROOT/'results/RQ3a-reset-recovery-v1/recovery-acceptance.json'
    assert json.loads(recovery.read_text())['accepted']
    regression = ex.ROOT/'results/RQ3a-reset-recovery-v1/unit-acceptance.json'
    checked = json.loads(regression.read_text())
    assert checked['accepted'] and checked['reset_boundary_tests_passed'] == 4
    for name, digest in checked['source_hashes'].items():
        assert ex.sha(ex.ROOT/'tools'/name) == digest, name
    for name, digest in old['source_hashes'].items():
        assert ex.sha(ex.ROOT/'tools'/name) == digest, name
    for case in old['cases'][:3]:
        checked = audit_case(parent/case['name'], case)
        assert checked['accepted'], checked['failures']
    cases, sources = prior.CASES, prior.NEW_SOURCES
    try:
        prior.CASES = [(c['name'], c['method'], c['scenario'], c['initial_decision'],
                        c['execution_permitted']) for c in old['cases']]
        prior.NEW_SOURCES = sources + ['prepare_rq3a_response_pilots_v2.py',
            'rq3a_incident_reset.py', 'test_rq3a_incident_reset.py',
            'run_rq3a_response_pilots_v3.py', Path(__file__).name]
        prior.prepare(out)
    finally:
        prior.CASES, prior.NEW_SOURCES = cases, sources
    plan = json.loads((out/'plan.json').read_text())
    for case in plan['cases'][:3]:
        source = (parent/case['name']).resolve()
        clone = out/case['name']
        assert not (clone/'acceptance.json').exists()
        shutil.rmtree(clone)  # Unused prospective clone only; no trial data exist.
        clone.symlink_to(os.path.relpath(source, out), target_is_directory=True)
        case.update(carried_from_directory=str(source.relative_to(ex.ROOT)),
                    carried_acceptance_sha256=ex.sha(source/'acceptance.json'))
    plan['prospective_reset_amendment'] = {'at': time.time(),
        'parent_plan': str((parent/'plan.json').relative_to(ex.ROOT)),
        'parent_plan_sha256': ex.sha(parent/'plan.json'),
        'rejected_attempt': str(rejected_path.relative_to(ex.ROOT)),
        'rejected_attempt_sha256': ex.sha(rejected_path),
        'recovery_acceptance': str(recovery.relative_to(ex.ROOT)),
        'recovery_acceptance_sha256': ex.sha(recovery),
        'scope': 'Declined incidents and exceptional cleanup now remove only the already-declared '
                 'target Deployment before the unchanged fixed-seed baseline reset. '
                 'The same post-decision operation already existed in the physical repair executor.',
        'candidate_action_changed': False, 'decision_rule_changed': False,
        'observation_windows_changed': False, 'accepted_cases_carried': 3,
        'previous_failed_reset_not_relabelled': True}
    ex.save(out/'plan.json', plan)
    for name in ('reuse-flow-regressions.log', 'certificate-provenance-regressions.json',
                 'registration-boundary-tests-v2.log'):
        (out/name).write_bytes((parent/name).read_bytes())
    units = json.loads((parent/'unit-acceptance.json').read_text())
    units.update(reset_boundary_tests_passed=4, reset_test_receipt=str(regression.relative_to(ex.ROOT)),
                 reset_test_receipt_sha256=ex.sha(regression))
    ex.save(out/'unit-acceptance.json', units)
    ex.save(out/'progress.json', {'at': time.time(), 'state': 'prepared',
        'pilots_completed': 3, 'pilots_planned': 5, 'formal_responses_completed': 0,
        'next': 'Fresh fourth pilot and fifth pilot; three accepted physical cases retained'})
    print('Prepared scoped reset retry; retained three independently accepted pilots unchanged.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output.resolve())

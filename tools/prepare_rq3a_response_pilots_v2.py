"""Prospective correction of an incorrect negative-pilot case expectation.

The matching threshold and every runtime scientific source remain unchanged.
The complete rejected matching/failed-repair attempt is retained; the new
nonmatching case is selected using an earlier accepted development observation.
"""
import argparse
import json
import os
import shutil
import time
from pathlib import Path

import comparison_policies as policies
import incident_observations as incidents
import prepare_rq3a_response_pilots as prior
import reuse_flow_executor as ex


def prepare(out):
    parent = ex.ROOT/'results/RQ3a-controller-pilots-v1'
    rejected = parent/'02-fault-decline-P4/acceptance.json'
    rejection = json.loads(rejected.read_text())
    assert not rejection['passed'] and rejection['primary_post_reset_confirmed']
    assert rejection['workflow']['initial_decision'] == 'Direct reuse'
    assert rejection['live_executor_audit']['accepted']
    assert rejection['live_outcome'] == 'Confirmed failure'
    old = json.loads((parent/'plan.json').read_text())
    for name, digest in old['source_hashes'].items():
        assert ex.sha(ex.ROOT/'tools'/name) == digest, name
    first = parent/'01-fault-direct-P5'
    assert json.loads((first/'acceptance.json').read_text())['passed']
    evidence = ex.ROOT/'results/reuse-flow-development-v8/02-revalidate-P2/primary/incident-setup.json'
    setup = json.loads(evidence.read_text())
    incident = incidents.observed_incident(setup['fault_persistence'][0]['target'], 'ratings')
    model_path = ex.ROOT/'results/formal-main-v1/fault-matching-model.json'
    model = json.loads(model_path.read_text())['model']
    score = policies.fault_matching(model, 'P2', incident)
    assert score['decision'] == 'Stop reuse'
    cases, sources = prior.CASES, prior.NEW_SOURCES
    try:
        prior.CASES = [cases[0], ('02-fault-decline-P2', 'fault_matching', 'P2-2-1', 'Stop reuse', False)] + cases[2:]
        prior.NEW_SOURCES = sources + [Path(__file__).name]
        prior.prepare(out)
    finally:
        prior.CASES, prior.NEW_SOURCES = cases, sources
    # This fresh unused clone contains no trial data. Reuse the immutable accepted
    # first pilot, never its old execution permission, with exact source identity.
    unused_clone = out/'01-fault-direct-P5'
    assert not (unused_clone/'acceptance.json').exists()
    shutil.rmtree(unused_clone)
    unused_clone.symlink_to(os.path.relpath(first, out), target_is_directory=True)
    plan = json.loads((out/'plan.json').read_text())
    plan['cases'][0].update(carried_from_directory=str(first.relative_to(ex.ROOT)),
                           carried_acceptance_sha256=ex.sha(first/'acceptance.json'))
    plan['preserved_rejected_pilot'] = {'path': str(rejected.relative_to(ex.ROOT)), 'sha256': ex.sha(rejected),
        'actual_initial_decision': 'Direct reuse', 'actual_live_outcome': 'Confirmed failure',
        'primary_reset_confirmed': True,
        'reason': 'The original prospective expectation of a nonmatch was wrong; the fixed model and actual failed repair are preserved unchanged.'}
    plan['prospective_case_amendment'] = {'at': time.time(), 'scope': 'Controller acceptance case only, before formal response collection',
        'previous_negative_case': 'P4-1-2', 'replacement_negative_case': 'P2-2-1',
        'development_observation': str(evidence.relative_to(ex.ROOT)), 'development_observation_sha256': ex.sha(evidence),
        'fixed_model_sha256': ex.sha(model_path), 'actual_development_score': score,
        'threshold_recalibrated': False, 'completed_RQ1_outcomes_used_for_selection': False,
        'accepted_first_pilot_carried_without_reexecution_or_permission_reuse': True}
    ex.save(out/'plan.json', plan)
    for name in ('unit-acceptance.json', 'reuse-flow-regressions.log', 'certificate-provenance-regressions.json',
                 'registration-boundary-tests-v2.log'):
        (out/name).write_bytes((parent/name).read_bytes())
    print('Prepared corrected pilot case; original actual failed repair retained; model and threshold unchanged.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output.resolve())

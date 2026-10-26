"""Prospective pre-main contracts. Does not register or run main trials."""
import argparse
import copy
import hashlib
import json
import time
from pathlib import Path

import yaml
import scenario_feasibility as s

ROOT = s.ROOT
REVISION_IDS = ['P2-2-1', 'P2-2-2', 'P3-2-1']


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def prepare(out):
    if out.exists():
        raise RuntimeError('Preserve existing prospective development protocol')
    reqpath = ROOT / 'candidates/requirements.S3-feasible.v1.yaml'
    req = yaml.safe_load(reqpath.read_text())
    original = json.loads((ROOT / 'results/S3-feasibility-v1/protocol.json').read_text())
    registered = json.loads((ROOT / 'candidates/registration.v1.json').read_text())
    assert registered['registered_candidates'] == 5
    assert registered['main_replay_trials'] == 0
    rows = []
    out.mkdir(parents=True)
    for proposal in req['proposals']:
        record = ROOT / proposal['feasibility_record']
        assert sha(record) == proposal['feasibility_record_sha256']
        accepted = json.loads(record.read_text())
        assert accepted['passed']
        row = copy.deepcopy(next(x for x in original['scenarios'] if x['id'] == proposal['id']))
        if row['id'] == 'P3-2-2':
            row = json.loads((ROOT / 'results/S3-rollout-constraint-v2/protocol.json').read_text())['scenario']
        assert sha(ROOT / row['healthy_manifest']) == row['manifest_sha256']
        row['construction'] = copy.deepcopy(proposal['construction'])
        docs = list(yaml.safe_load_all((ROOT / row['healthy_manifest']).read_text()))
        row['development_revision'] = None
        if row['id'] in ('P2-2-1', 'P2-2-2'):
            row['construction']['fault_replicas'] = 0
            row['development_revision'] = ('Use replicas=0 in both variant and original-condition control. '
                'Original minimum=1 with replicas=1 has no fault. Retain variant minima 2/3; '
                'the earlier preserving expectation for P2-2-1 is withdrawn, not used as an outcome label.')
        if row['id'] == 'P3-2-1':
            row['construction']['fault_memory'] = '1Mi'
            d = s.obj(docs, 'Deployment', 'web')
            d['spec']['template']['spec'].setdefault('affinity', {})['nodeAffinity'] = {
                'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                    {'matchExpressions': [{'key': 'beta.kubernetes.io/os', 'operator': 'In',
                                           'values': ['linux', 'ddn-unused-platform']}]}]}}
            row['development_revision'] = ('Replace fault-strength-only difference with a changed, admitted '
                'nodeAffinity value set containing linux. Keep the same 1Mi fault in variant and control; '
                'the frozen action still writes its original linux value. Requires new construction validation.')
        path = out / (row['id'] + '.healthy.yaml')
        path.write_text(yaml.safe_dump_all(docs, sort_keys=False, width=4096))
        row.update(healthy_manifest=str(path.relative_to(ROOT)), manifest_sha256=sha(path),
                   source_feasibility_record=proposal['feasibility_record'],
                   source_feasibility_sha256=proposal['feasibility_record_sha256'],
                   main_registered=False, outcome_label=None)
        # Expectations are deliberately excluded from executable evaluator/policy inputs.
        row.pop('expected_effect', None)
        row['non_target_workloads'] = sorted(d['metadata']['name'] for d in docs
            if d and d['kind'] in ('Deployment', 'StatefulSet')
            and d['metadata']['name'] != row['construction']['target_deployment'])
        row['control_id'] = row['candidate'] + '-original-conditions'
        rows.append(row)
    controls = []
    for key in sorted(req['candidate_hashes']):
        row = {'id': key + '-original-conditions', 'candidate': key, 'condition': 'control'}
        docs, cfg = s.variant(row)
        row.update(construction=cfg, healthy_manifest='manifests/qualification.v3.yaml',
                   manifest_sha256=sha(s.BASE), main_registered=False, outcome_label=None,
                   non_target_workloads=sorted(d['metadata']['name'] for d in docs
                       if d and d['kind'] in ('Deployment', 'StatefulSet')
                       and d['metadata']['name'] != cfg['target_deployment']))
        controls.append(row)
    protocol = {'version': 'pre-main-development-v1', 'written_at': time.time(),
        'stage': 'development; not main freeze', 'context': 'k3d-lab', 'namespace': 'robot-shop',
        'manifest': 'manifests/qualification.v3.yaml', 'manifest_sha256': sha(s.BASE),
        'requirements_source': str(reqpath.relative_to(ROOT)), 'requirements_sha256': sha(reqpath),
        'registration_sha256': sha(ROOT/'candidates/registration.v1.json'),
        'candidate_hashes': req['candidate_hashes'], 'scenarios': rows, 'controls': controls,
        'revisions_requiring_new_feasibility': REVISION_IDS,
        'main_protocol_ready': False, 'registered_main_scenarios': 0, 'main_trials': 0,
        'timing': {'healthy_window': 15, 'fault_confirmation': 90, 'pre_action': 60,
                   'repair_budget': 120, 'outcome_window': 210, 'sustained_recovery': 30,
                   'sample_interval': 5, 'non_target_interval': 15, 'pre_check_max_age': 30,
                   'launch_after_pre_check_max_seconds': 5},
        'control_policy': 'Each variant and its original-condition control use the identical family fault '
             'parameters (P2=0 replicas, P3=1Mi). Original conditions retain the registered minimum=1. '
             'Control repeat count/order and any shared-control analysis must be frozen before main collection.',
        'non_target_policy': 'Named synchronous checks immediately before action; record preexisting '
             'instability separately. Regression requires that same check pass before and fail after action. '
             'Declaring a workload does not drop it when missing. Terminating old Pods are allowed only '
             'when current owner/template rollout and zero-restart current Pods pass.',
        'verification_policy': 'Current serving owner, current template, required replicas and EndpointSlice '
             'UID binding plus current business criteria. P4 relocation retains cart/vote consumer checks '
             'as explicit overlapping outcome criteria, rather than attributing them solely to one condition.',
        'vote_policy': original['vote_policy'], 'cart_policy': original['cart_policy'],
        'development_limits': 'No results in this directory count as main trials or as evidence of method superiority. '
             'All failed and incomplete attempts remain. Candidate action targets and inputs stay frozen.',
        'prerequisites': ['new construction checks', 'executor live acceptance', 'RQ2 implementation verification',
                          'labelled fault-matching pilot and threshold freeze', 'v3 replica fidelity',
                          'fixed replanning backend and sequence protocol']}
    (out / 'protocol.json').write_text(json.dumps(protocol, indent=2)+'\n')
    print(json.dumps({'prepared_scenarios':len(rows), 'original_condition_controls':len(controls),
                      'new_construction_checks':REVISION_IDS, 'main_trials':0}))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, default=ROOT/'results/pre-main-development-v1')
    prepare(ap.parse_args().output.resolve())

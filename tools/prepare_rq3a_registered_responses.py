"""Register the approved matched response queue only after independent pilot acceptance."""
import argparse
import copy
import json
import os
import time
from pathlib import Path

import yaml
import reset_baseline as rb
import reuse_flow_executor as ex
from audit_rq3a_response_pilots import audit as audit_pilots
from prepare_replica_development import translate


def prepare(out, pilots):
    independent = json.loads((pilots/'independent-acceptance.json').read_text())
    pilot_acceptance = json.loads((pilots/'final-acceptance.json').read_text())
    units = json.loads((pilots/'unit-acceptance.json').read_text())
    translations = json.loads((pilots/'translation-acceptance.json').read_text())
    assert independent['accepted'] and len(independent['records']) == 5
    assert pilot_acceptance['accepted'] and pilot_acceptance['pilots'] == 5
    assert units['accepted'] and translations['accepted'] and translations['constructions'] == 40
    rechecked = audit_pilots(pilots)
    assert rechecked['accepted'] and rechecked['plan_sha256'] == independent['plan_sha256']
    for prior, current in zip(independent['records'], rechecked['records']):
        assert all(prior.get(k) == current.get(k) for k in
            ('case', 'method', 'acceptance_sha256', 'workflow_sha256', 'initial_decision', 'final_decision'))
    plan_path = ex.ROOT/'results/RQ3a-response-preparation-v2/plan.json'
    approved = json.loads(plan_path.read_text())
    assert ex.sha(plan_path) == json.loads((plan_path.parent/'preparation-acceptance.json').read_text())['plan_sha256']
    predecessor = ex.ROOT/approved['predecessor']
    assert ex.sha(predecessor) == approved['predecessor_sha256']
    original = json.loads(predecessor.read_text())
    assert ex.sha(ex.ROOT/'sources/sources.lock.yaml') == original['source_lock_sha256']
    for name, digest in original['tool_hashes'].items():
        assert ex.sha(ex.ROOT/'tools'/name) == digest, name
    out.mkdir(exist_ok=False)
    archive = out/'tool-snapshot'; archive.mkdir()
    sources = sorted((ex.ROOT/'tools').glob('*.py')) + [ex.ROOT/'tools/ansible-inventory.ini']
    source_hashes = {f.name: ex.sha(f) for f in sources}
    for source in sources:
        (archive/source.name).write_bytes(source.read_bytes())
    cases = []
    rows = {r['id']: r for r in approved['scenario_definitions']}
    docs = list(yaml.safe_load_all((ex.ROOT/original['manifest']).read_text()))
    for trial in approved['queue']:
        name = f"{trial['position']:03d}-{trial['trial_id']}"
        directory = out/name; directory.mkdir()
        row = copy.deepcopy(rows[trial['scenario']])
        assert ex.sha(ex.ROOT/row['healthy_manifest']) == row['manifest_sha256']
        healthy = list(yaml.safe_load_all((ex.ROOT/row['healthy_manifest']).read_text()))
        protocols = {}
        for role, context in [('primary', 'k3d-lab'), ('replica', 'k3d-lab-validation')]:
            child = directory/role; child.mkdir()
            baseline, variant = child/'baseline.yaml', child/'healthy.yaml'
            baseline.write_text(yaml.safe_dump_all(translate(docs) if role == 'replica' else docs,
                                                  sort_keys=False, width=4096))
            variant.write_text(yaml.safe_dump_all(translate(healthy) if role == 'replica' else healthy,
                                                 sort_keys=False, width=4096))
            rb.MANIFEST = baseline
            ex.save(baseline.with_suffix('.fingerprint.json'), rb.declared_fingerprint())
            selected = copy.deepcopy(row)
            selected.update(healthy_manifest=str(variant.relative_to(ex.ROOT)), manifest_sha256=ex.sha(variant))
            selected.pop('main_registered', None); selected.pop('outcome_label', None)
            protocol = copy.deepcopy(original)
            protocol.update(version='formal-rq3a-response-v1-'+name+'-'+role,
                stage='registered RQ3a method-controlled response component', context=context,
                manifest=str(baseline.relative_to(ex.ROOT)), manifest_sha256=ex.sha(baseline),
                scenarios=[selected], controls=[], order=[], main_trials=0,
                registered_main_scenarios=0, main_protocol_ready=False,
                main_trial_count_scope='RQ1 independent counterfactual replays only; this component adds none',
                response_registered=True, trial_identity=copy.deepcopy(trial), response_method=trial['method'],
                authoritative_incident_record=str((directory/'acceptance.json').relative_to(ex.ROOT)),
                criterion_timing=copy.deepcopy(original['timing']),
                decision_workflow_configuration=copy.deepcopy(approved['decision_configuration']),
                parent_protocol=str(predecessor.relative_to(ex.ROOT)), parent_protocol_sha256=ex.sha(predecessor))
            protocol['timing']['pre_action'] = 0 if role == 'primary' else 60
            protocol['timing_policy'] = ('Primary has the separately recorded60s pre-decision fault-persistence '
                'period; immediate live permission consumption follows. Replica retains its own60s pre-action '
                'period. Both use the unchanged210s outcome and30s sustained-recovery requirements. '
                'Canonical criterion comparison uses unchanged logical criterion_timing.')
            ex.save(child/'protocol.json', protocol)
            (child/'tool-snapshot').symlink_to(os.path.relpath(archive, child), target_is_directory=True)
            ex.save(child/'executor-freeze.json', {'at': time.time(),
                'stage': 'raw reusable executor component under registered RQ3a incident wrapper',
                'protocol_sha256': ex.sha(child/'protocol.json'),
                'tool_hashes': {n: h for n, h in source_hashes.items() if n.endswith('.py')},
                'inventory_sha256': source_hashes['ansible-inventory.ini']})
            protocols[role] = ex.sha(child/'protocol.json')
        cases.append({**trial, 'name': name, 'protocol_sha256': protocols})
    protocol = {'version': 'formal-rq3a-response-v1', 'registered_at': time.time(),
        'response_registered': True, 'response_trials': 240, 'main_trials': 0,
        'rq1_collection_unchanged': True, 'scenarios': 40, 'policies': 3, 'repeats': 2,
        'isolated_replays_maximum': 160, 'queue': cases, 'configuration': approved['decision_configuration'],
        'approved_plan': str(plan_path.relative_to(ex.ROOT)), 'approved_plan_sha256': ex.sha(plan_path),
        'parent_protocol': str(predecessor.relative_to(ex.ROOT)), 'parent_protocol_sha256': ex.sha(predecessor),
        'source_hashes': source_hashes, 'pilot_directory': str(pilots.relative_to(ex.ROOT)),
        'pilot_evidence': {n: ex.sha(pilots/n) for n in ('plan.json', 'final-acceptance.json',
            'independent-acceptance.json', 'unit-acceptance.json', 'translation-acceptance.json')},
        'inputs': approved['inputs'], 'candidate_hashes': approved['candidate_hashes'],
        'registration_sha256': ex.sha(ex.ROOT/'candidates/registration.v1.json'),
        'source_lock': {'path': 'sources/sources.lock.yaml', 'sha256': original['source_lock_sha256']},
        'input_safety': approved['outcome_leakage_control'],
        'counter_scope': '240 actual method-controlled incident responses; isolated validation actions and pilot attempts counted separately.',
        'raw_trace_codec': 'Unchanged physical executor components retain their legacy zero-RQ1/development codec. '
            'Only the explicitly registered outer formal_rq3a_method_response record counts an incident; '
            'no earlier pilot or RQ1 record is relabelled or copied as a new response.',
        'unknown_validation_policy': 'No successful certificate is invented. Safely cancelled/incomplete validation '
            'declines live execution; independent cleanup must pass. Complete isolated failures are reported separately.',
        'interruption_policy': 'Preserve every executed Indeterminate and pre-action rejection. Halt on incomplete '
            'primary execution evidence or unconfirmed reset. An explicit separately archived retry never erases the first attempt.',
        'analysis_scope': approved['analysis_scope'], 'rq3b_started': False,
        'disk_guard_minimum_free_bytes': 2 * 1024**3,
        'cleanup_policy': 'After240 independent accepted response audits and final resets, stop only the eight '
            'retained experiment node/load-balancer containers. Preserve registry, images, volumes, evidence and other projects.'}
    ex.save(out/'protocol.json', protocol)
    ex.save(out/'freeze.json', {'at': time.time(), 'protocol_sha256': ex.sha(out/'protocol.json'),
                              'source_hashes': source_hashes, 'immutable': True})
    ex.save(out/'progress.json', {'state': 'registered', 'at': time.time(), 'position': 0,
        'responses_completed': 0, 'responses_planned': 240, 'formal_registered': True})
    print('Registered240 actual response incidents; pilot and RQ1 evidence remain separate.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pilots', type=Path, required=True); args = parser.parse_args()
    prepare(args.output.resolve(), args.pilots.resolve())

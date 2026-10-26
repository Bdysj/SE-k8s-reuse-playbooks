"""Freeze separately counted controller pilots and all forty role translations."""
import argparse
import copy
import json
import time
from pathlib import Path

import yaml
import reset_baseline as rb
import reuse_flow_executor as ex
from prepare_replica_development import translate, ROLE_MAP

CASES = [
    ('01-fault-direct-P5', 'fault_matching', 'P5-original-conditions', 'Direct reuse', True),
    ('02-fault-decline-P4', 'fault_matching', 'P4-1-2', 'Stop reuse', False),
    ('03-whole-pass-P2', 'whole_environment', 'P2-2-1', 'Revalidate', True),
    ('04-whole-decline-P5', 'whole_environment', 'P5-2-2', 'Revalidate', False),
    ('05-proposed-direct-P5', 'proposed', 'P5-original-conditions', 'Direct reuse', True),
]
NEW_SOURCES = ['rq3a_response_workflow.py', 'test_rq3a_response_workflow.py',
               'prepare_rq3a_response_pilots.py', 'run_rq3a_response_pilots.py']


def prepare(out):
    predecessor = ex.ROOT/'results/formal-main-v1/protocol.json'
    original = json.loads(predecessor.read_text())
    plan_path = ex.ROOT/'results/RQ3a-response-preparation-v2/plan.json'
    plan = json.loads(plan_path.read_text())
    acceptance = json.loads((plan_path.parent/'preparation-acceptance.json').read_text())
    assert ex.sha(plan_path) == acceptance['plan_sha256']
    assert ex.sha(predecessor) == plan['predecessor_sha256']
    assert plan['counts']['planned_responses'] == 240
    for name, digest in original['tool_hashes'].items():
        assert ex.sha(ex.ROOT/'tools'/name) == digest, name
    for data in plan['inputs'].values():
        assert ex.sha(ex.ROOT/data['path']) == data['sha256']
    for key, digest in plan['candidate_hashes'].items():
        assert ex.sha(ex.ROOT/'candidates'/(key+'.yaml')) == digest
    out.mkdir(exist_ok=False)
    translations = out/'construction-translations'; translations.mkdir()
    audits = []
    inverse = {v: k for k, v in ROLE_MAP.items()}
    def reverse(value):
        if isinstance(value, dict):
            return {reverse(k): reverse(v) for k, v in value.items()}
        if isinstance(value, list):
            return [reverse(v) for v in value]
        return inverse.get(value, value) if isinstance(value, str) else value
    for row in plan['scenario_definitions']:
        source = ex.ROOT/row['healthy_manifest']
        assert ex.sha(source) == row['manifest_sha256']
        docs = list(yaml.safe_load_all(source.read_text()))
        mapped = translate(docs)
        path = translations/(row['id']+'.replica.yaml')
        path.write_text(yaml.safe_dump_all(mapped, sort_keys=False, width=4096))
        roundtrip = reverse(list(yaml.safe_load_all(path.read_text()))) == docs
        cfg = row['construction']
        arithmetic = (row['candidate'] != 'P2' or
            cfg['fault_operator'] == 'decrement_replicas_clamp_zero'
            and cfg['fault_decrement'] == 2
            and cfg['fault_replicas'] == max(0, cfg['healthy_replicas_before_fault'] - 2))
        assert roundtrip and arithmetic, row['id']
        audits.append({'scenario': row['id'], 'source': str(source.relative_to(ex.ROOT)),
            'source_sha256': ex.sha(source), 'replica_manifest': str(path.relative_to(ex.ROOT)),
            'replica_manifest_sha256': ex.sha(path), 'only_exact_node_role_names_translated': roundtrip,
            'relative_fault_arithmetic_consistent': arithmetic, 'passed': True})
    sources = list(dict.fromkeys(list(original['tool_hashes']) + NEW_SOURCES))
    cases = []
    for name, method, identity, expected, permitted in CASES:
        directory = out/name; directory.mkdir()
        row = copy.deepcopy(next(r for r in original['scenarios'] + original['controls'] if r['id'] == identity))
        assert ex.sha(ex.ROOT/row['healthy_manifest']) == row['manifest_sha256']
        for role, context in [('primary', 'k3d-lab'), ('replica', 'k3d-lab-validation')]:
            child = directory/role; child.mkdir()
            documents = list(yaml.safe_load_all((ex.ROOT/original['manifest']).read_text()))
            healthy = list(yaml.safe_load_all((ex.ROOT/row['healthy_manifest']).read_text()))
            if role == 'replica':
                documents, healthy = translate(documents), translate(healthy)
            baseline, variant = child/'baseline.yaml', child/'healthy.yaml'
            baseline.write_text(yaml.safe_dump_all(documents, sort_keys=False, width=4096))
            variant.write_text(yaml.safe_dump_all(healthy, sort_keys=False, width=4096))
            rb.MANIFEST = baseline
            ex.save(baseline.with_suffix('.fingerprint.json'), rb.declared_fingerprint())
            selected = copy.deepcopy(row)
            selected.update(healthy_manifest=str(variant.relative_to(ex.ROOT)), manifest_sha256=ex.sha(variant))
            protocol = copy.deepcopy(original)
            protocol.update(version=out.name+'-'+name+'-'+role, context=context,
                manifest=str(baseline.relative_to(ex.ROOT)), manifest_sha256=ex.sha(baseline),
                scenarios=[selected], controls=[], main_trials=0, registered_main_scenarios=0,
                main_protocol_ready=False, record_type='rq3a_controller_pilot', written_at=time.time(),
                criterion_timing=copy.deepcopy(original['timing']),
                decision_workflow_configuration=copy.deepcopy(plan['decision_configuration']),
                response_method=method, parent_protocol=str(predecessor.relative_to(ex.ROOT)),
                parent_protocol_sha256=ex.sha(predecessor))
            protocol['timing']['pre_action'] = 0 if role == 'primary' else 60
            protocol['timing_policy'] = ('Primary separately completes the same sixty-second fault-persistence '
                'period before decision; permission is consumed immediately. Replica has its own sixty-second '
                'pre-action period and210s complete outcome. Criterion comparison retains original logical '
                'timing, rather than treating this executor partition as a changed recovery criterion.')
            ex.save(child/'protocol.json', protocol)
            old_sources = ex.SOURCES
            try:
                ex.SOURCES = sources; ex.freeze(child)
            finally:
                ex.SOURCES = old_sources
        cases.append({'name': name, 'method': method, 'scenario': identity,
            'initial_decision': expected, 'execution_permitted': permitted,
            'scope': 'Prospective controller acceptance; no new model fitting or main response result.'})
    archive = out/'tool-snapshot'; archive.mkdir()
    for name in sources:
        (archive/name).write_bytes((ex.ROOT/'tools'/name).read_bytes())
    ex.save(out/'translation-acceptance.json', {'at': time.time(), 'accepted': True,
        'constructions': 40, 'records': audits, 'role_mapping': ROLE_MAP,
        'scope': 'Static full-spec translation and fault arithmetic only. Every actual replica '
                 'must additionally pass current live full-spec, storage, node, fault and outcome fidelity before its certificate is consumed.'})
    ex.save(out/'plan.json', {'version': out.name, 'at': time.time(), 'main_trials': 0,
        'formal_registered': False, 'cases': cases, 'configuration': plan['decision_configuration'],
        'response_plan': str(plan_path.relative_to(ex.ROOT)), 'response_plan_sha256': ex.sha(plan_path),
        'source_hashes': {n: ex.sha(ex.ROOT/'tools'/n) for n in sources},
        'translation_audit_sha256': ex.sha(out/'translation-acceptance.json'),
        'original_scientific_sources_unchanged': True,
        'permitted_incident_input': 'First actual target sample during pre-action fault persistence; workload/symptoms/error codes only.',
        'fidelity_normalisation': 'Same as unchanged Adapter: exact node roles, cluster-assigned Service IPs and undeclared external nodePorts only.',
        'baseline_admission': 'No proposed condition states used to permit or deny either baseline.',
        'retry_policy': 'A rejected pilot halts. Preserve its logs and reset confirmations; use a separate prospective version for a corrected retry.'})
    ex.save(out/'progress.json', {'at': time.time(), 'state': 'prepared', 'pilots_completed': 0,
                                  'pilots_planned': len(cases), 'formal_responses_completed': 0})
    print('Prepared five independent controller pilots and forty exact role translations.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output.resolve())

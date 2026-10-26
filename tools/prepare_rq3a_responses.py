"""Prepare matched RQ3a trial identities without reusing repair outcome labels.

This is a preparation record, not a ready-to-run scientific protocol. Executor
boundary verification and live comparison pilots must pass before registration.
"""
import collections
import hashlib
import json
import random
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METHODS = ('proposed', 'fault_matching', 'whole_environment')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def main():
    source = ROOT / 'results/formal-main-v1/protocol.json'
    p = json.loads(source.read_text())
    analysis = ROOT / 'results/formal-main-analysis-v1/final-audit.json'
    audit = json.loads(analysis.read_text())
    assert audit['accepted'] and audit['main'] == 200 and audit['controls'] == 95
    assert p['additional_runs']['future_actual_RQ3a_response_trials_planned'] == 600
    for name, digest in p['tool_hashes'].items():
        assert sha(ROOT / 'tools' / name) == digest, name
    for name, digest in p['candidate_hashes'].items():
        assert sha(ROOT / 'candidates' / (name + '.yaml')) == digest, name
    for key in ('manifest', 'comparison_model', 'whole_environment_references'):
        assert sha(ROOT / p[key]) == p[key + '_sha256'], key
    scenarios = p['scenarios']
    assert len(scenarios) == 40 and len({r['id'] for r in scenarios}) == 40
    assert collections.Counter(r['candidate'] for r in scenarios) == {k: 8 for k in p['candidate_hashes']}
    for row in scenarios:
        assert sha(ROOT / row['healthy_manifest']) == row['manifest_sha256']
    out = ROOT / 'results/RQ3a-response-preparation-v1'
    out.mkdir(exist_ok=False)
    rng = random.Random(20261006)
    queue = []
    # The same scenario/repeat constitutes a matched block. Each method receives
    # a fresh physical setup. Method order is shuffled before any response data.
    for repeat in range(1, 6):
        cases = sorted(scenarios, key=lambda r: r['id'])
        rng.shuffle(cases)
        for row in cases:
            methods = list(METHODS)
            rng.shuffle(methods)
            for method in methods:
                queue.append({'position': len(queue) + 1, 'scenario': row['id'],
                              'candidate': row['candidate'], 'repeat': repeat, 'method': method,
                              'matched_block': row['id'] + '-r' + str(repeat),
                              'trial_id': row['id'] + '-r' + str(repeat) + '-' + method})
    assert len(queue) == len({r['trial_id'] for r in queue}) == 600
    assert collections.Counter(r['method'] for r in queue) == {m: 200 for m in METHODS}
    grouped = collections.defaultdict(list)
    for row in queue: grouped[row['matched_block']].append(row['method'])
    assert len(grouped) == 200 and all(set(ms) == set(METHODS) for ms in grouped.values())
    plan = {'version': 'rq3a-response-preparation-v1', 'prepared_at': time.time(),
            'stage': 'prepared_not_registered_or_started', 'formal_protocol_ready': False,
            'predecessor': str(source.relative_to(ROOT)), 'predecessor_sha256': sha(source),
            'completed_collection_audit': str(analysis.relative_to(ROOT)),
            'completed_collection_audit_sha256': sha(analysis),
            'counts': {'policies': 3, 'scenarios': 40, 'repeats': 5, 'planned_responses': 600,
                       'isolated_replays_maximum': 400, 'actual_responses_collected': 0},
            'order_random_seed': 20261006, 'candidate_hashes': p['candidate_hashes'],
            'decision_configuration': p['decision_configuration'], 'outcome_timing': p['timing'],
            'inputs': {key: {'path': p[key], 'sha256': p[key+'_sha256']}
                       for key in ('manifest','comparison_model','whole_environment_references')},
            'scenario_definitions': scenarios, 'queue': queue,
            'policy_routes': {
                'proposed': 'Unchanged frozen requirement comparator and bounded workflow; refresh and isolated replay only for unresolved evidence.',
                'fault_matching': 'Unchanged fitted model; matching permits execution through the common gate, nonmatching declines. Do not add proposed condition logic to this baseline.',
                'whole_environment': 'Unchanged canonical comparison; difference requests full isolated replay. A fresh successful, faithful replay may permit reuse despite proposed Contradicted states. Confirmed failure, incomplete evidence or exhausted budget declines.'},
            'shared_execution_controls': [
                'Same frozen candidate, allowed operations, physical executor and current outcome predicates.',
                'Separate confirmed reset and fixed seed before every method trial.',
                'Retain initial choice, actual refresh/replay counts, final route, permission evidence and all costs.',
                'Immediately before action verify candidate identity, fresh evidence and unchanged relevant revision.',
                'Full replica fidelity and independent completed-window outcome audit before any validation certificate is consumed.',
                'Do not import proposed requirement states into the fault-matching or whole-environment permission rule.',
                'Stop/decline means Not executed; unavailable evidence means Indeterminate for an executed attempt, not successful repair.',
                'Retain incomplete executions and setup rejections; halt on unconfirmed cleanup/reset.',
                'P1 fault must remain active through the decision and complete live observation window.'],
            'analysis_scope': 'Matched scenario/repeat groups; report initial Direct reuse and reuse after revalidation separately. Existing independent RQ1 replays identify opportunities without controlling new decisions. RQ3b sequence and replanning timing are separate.',
            'outcome_leakage_control': 'Queue and policy settings derived from the original frozen protocol; no main outcome labels, analysis rates or predicted effects enter policy inputs or order.',
            'registration_prerequisites_pending': [
                'Implement independent baseline response controllers and actual live boundary checks.',
                'Verify failed, expired, wrong-candidate and changed-revision validation certificates cannot permit action.',
                'Run separately recorded live pilots for both baseline controllers; preserve rejected pilots.',
                'Verify translated scenario fidelity and fault arithmetic across all declared constructions before using replica results.',
                'Freeze controller sources, pilot receipts, translated protocols and independent trial audits, then register the response queue.'],
            'pilot_scope': 'Baseline controller acceptance only; no main response result and no recalibration of candidate actions, comparison model, requirements or thresholds.'}
    write_new(out/'plan.json', plan)
    receipt = {'at': time.time(), 'prepared': True, 'formal_ready': False, 'responses_started': 0,
               'matched_blocks': 200, 'queue_entries': 600, 'scenario_count': 40,
               'all_original_scientific_sources_unchanged': True,
               'plan_sha256': sha(out/'plan.json'), 'preparation_script_sha256': sha(Path(__file__)),
               'scope': 'Identity and order preparation, not live executor acceptance or data collection.'}
    write_new(out/'preparation-acceptance.json', receipt)
    (out/'tool-snapshot').mkdir()
    (out/'tool-snapshot/prepare_rq3a_responses.py').write_bytes(Path(__file__).read_bytes())
    write_new(out/'progress.json', {'state': 'preparing', 'at': time.time(), 'position': 0,
                                  'responses_completed': 0, 'responses_planned': 600,
                                  'formal_registered': False,
                                  'next': 'baseline controller implementation and live pilot acceptance'})
    print(json.dumps(receipt))


if __name__ == '__main__': main()

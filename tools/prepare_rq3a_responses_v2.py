"""Record the user-approved two-repeat RQ3a amendment before response data.

The original six-hundred-entry preparation and completed RQ1 collection stay
immutable. This amendment changes response repetition counts only; it does not
register a live executor or infer trial outcomes.
"""
import collections
import copy
import hashlib
import json
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
    prior = ROOT / 'results/RQ3a-response-preparation-v1'
    old = json.loads((prior / 'plan.json').read_text())
    accepted = json.loads((prior / 'preparation-acceptance.json').read_text())
    progress = json.loads((prior / 'progress.json').read_text())
    assert accepted['prepared'] and not accepted['formal_ready']
    assert accepted['responses_started'] == progress['responses_completed'] == 0
    assert not progress['formal_registered']
    assert sha(prior / 'plan.json') == accepted['plan_sha256']
    assert old['counts']['planned_responses'] == 600
    source = ROOT / old['predecessor']
    assert sha(source) == old['predecessor_sha256']
    p = json.loads(source.read_text())
    for name, digest in p['tool_hashes'].items():
        assert sha(ROOT / 'tools' / name) == digest, name
    for name, digest in p['candidate_hashes'].items():
        assert sha(ROOT / 'candidates' / (name + '.yaml')) == digest, name
    for data in old['inputs'].values():
        assert sha(ROOT / data['path']) == data['sha256']
    for row in old['scenario_definitions']:
        assert sha(ROOT / row['healthy_manifest']) == row['manifest_sha256']
    # Both complete repeat blocks retain their earlier prospective random order.
    # No outcome, decision or failure prevalence participates in this selection.
    queue = copy.deepcopy([r for r in old['queue'] if r['repeat'] in (1, 2)])
    assert queue == old['queue'][:240]
    assert len(queue) == len({r['trial_id'] for r in queue}) == 240
    assert [r['position'] for r in queue] == list(range(1, 241))
    assert collections.Counter(r['method'] for r in queue) == {m: 80 for m in METHODS}
    pairs = collections.defaultdict(list)
    for row in queue:
        pairs[(row['scenario'], row['repeat'])].append(row['method'])
    assert len(pairs) == 80 and all(set(v) == set(METHODS) for v in pairs.values())
    assert {k[0] for k in pairs} == {r['id'] for r in old['scenario_definitions']}
    assert all((r['id'], n) in pairs for r in old['scenario_definitions'] for n in (1, 2))
    out = ROOT / 'results/RQ3a-response-preparation-v2'
    out.mkdir(exist_ok=False)
    plan = copy.deepcopy(old)
    plan.update(version='rq3a-response-preparation-v2', prepared_at=time.time(),
                queue=queue, stage='amended_preparation_not_registered_or_started')
    plan['counts'].update(repeats=2, planned_responses=240,
                          isolated_replays_maximum=160, actual_responses_collected=0)
    plan['supersedes_preparation'] = {'path': str((prior / 'plan.json').relative_to(ROOT)),
                                     'sha256': sha(prior / 'plan.json')}
    plan['prospective_amendment'] = {
        'approved_instruction': '采纳240这个方案',
        'source': 'Direct user instruction in this conversation on 2026-10-06',
        'before_any_RQ3a_response_trial': True,
        'previous': {'repeats': 5, 'responses': 600, 'isolated_replays_maximum': 400},
        'current': {'repeats': 2, 'responses': 240, 'isolated_replays_maximum': 160},
        'reason': 'User-selected resource budget retaining all forty scenarios and all three policies.',
        'statistical_basis': 'A practical resource/coverage compromise, not a power-derived sufficiency threshold.',
        'unchanged': ['forty scenario definitions', 'three policies and fitted threshold',
                      'candidate actions and requirements', 'baseline and fixed seed',
                      'decision/revalidation budgets', 'current outcome predicates and observation windows',
                      'matched method blocks and earlier random order'],
        'completed_RQ1_replays_unchanged': 200,
        'completed_RQ1_repeats_per_scenario': 5,
        'analysis_limit': 'Two actual response repeats provide limited within-scenario variability evidence; report scenario/playbook clustering and no guaranteed reliability or timing precision.',
        'extra_work_counting': 'Live pilot attempts and actual isolated validation replays are counted separately from the240 method-controlled incident trials.'}
    write_new(out / 'plan.json', plan)
    (out / 'tool-snapshot').mkdir()
    (out / 'tool-snapshot/prepare_rq3a_responses_v2.py').write_bytes(Path(__file__).read_bytes())
    receipt = {'at': time.time(), 'prepared': True, 'formal_ready': False,
               'responses_started': 0, 'matched_blocks': 80, 'queue_entries': 240,
               'scenario_count': 40, 'policies': 3, 'repeats': 2,
               'all_original_scientific_sources_unchanged': True,
               'old_preparation_preserved': True,
               'plan_sha256': sha(out / 'plan.json'),
               'preparation_script_sha256': sha(Path(__file__)),
               'scope': 'Prospective count amendment and complete matched queue; not live executor acceptance or formal collection.'}
    write_new(out / 'preparation-acceptance.json', receipt)
    write_new(out / 'progress.json', {'state': 'preparing', 'at': time.time(), 'position': 0,
                                     'responses_completed': 0, 'responses_planned': 240,
                                     'formal_registered': False,
                                     'next': 'baseline controller implementation and live pilot acceptance'})
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()

"""Offline audit and descriptive analysis of the immutable RQ1 collection.

Saved initial policy choices are never relabelled as actual RQ3a responses.
Repeated observations remain grouped by scenario; rejected attempts remain visible.
"""
import argparse
import collections
import copy
import fcntl
import importlib.util
import json
import random
import statistics
import time
from pathlib import Path

import formal_collection_continuation_v3 as c
import formal_methods as methods
import reuse_assessor as assessor
import supported_reuse_v2 as supported
import comparison_policies as policies

ROOT = c.ROOT
METHODS = ('proposed', 'fault_matching', 'whole_environment')
CONDITIONS = assessor.CONDITIONS


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def groups(rows, key):
    result = collections.defaultdict(list)
    for row in rows:
        result[row[key]].append(row)
    return result


def tally(rows):
    return {'trials': len(rows), 'outcomes': dict(collections.Counter(x['outcome'] for x in rows)),
            'execution_incomplete': sum(x['execution_complete'] is False for x in rows),
            'target_not_recovered': sum(x['target_recovery'] is False for x in rows),
            'non_target_regression': sum(x['non_target_regression'] is True for x in rows)}


def decisions_table(rows, method):
    table = {}
    for decision in ('Direct reuse', 'Revalidate', 'Stop reuse'):
        selected = [x for x in rows if x['decisions'][method] == decision]
        table[decision] = dict(trials=len(selected),
            counterfactual_outcomes=dict(collections.Counter(x['outcome'] for x in selected)))
    successful = [x for x in rows if x['outcome'] == 'Successful repair']
    numerator = sum(x['decisions'][method] == 'Direct reuse' for x in successful)
    table['retained_initial_direct_opportunities'] = {'numerator': numerator,
        'denominator': len(successful), 'fraction': numerator / len(successful) if successful else None}
    return table


def percentile(values, p):
    values = sorted(values)
    position = (len(values) - 1) * p
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def bootstrap(rows, repetitions=5000):
    """Resample eight scenario clusters within each of five fixed playbooks."""
    scenarios = groups(rows, 'scenario')
    by_family = collections.defaultdict(list)
    for scenario, observations in scenarios.items():
        assert len(observations) == 5
        by_family[observations[0]['candidate']].append(scenario)
    assert len(by_family) == 5 and all(len(x) == 8 for x in by_family.values())
    rng = random.Random(20261006)
    values = {'RQ1_success_fraction': []}
    for method in METHODS:
        values[method + '_retained_initial_direct_opportunities'] = []
    for _ in range(repetitions):
        sample = [x for family in sorted(by_family)
                  for scenario in rng.choices(sorted(by_family[family]), k=8)
                  for x in scenarios[scenario]]
        successes = [x for x in sample if x['outcome'] == 'Successful repair']
        values['RQ1_success_fraction'].append(len(successes) / len(sample))
        if successes:
            for method in METHODS:
                values[method + '_retained_initial_direct_opportunities'].append(
                    sum(x['decisions'][method] == 'Direct reuse' for x in successes) / len(successes))
    return {'seed': 20261006, 'replicates': repetitions,
        'unit': 'scenario with all five repeats retained; stratified within each fixed playbook',
        'scope': 'Uncertainty conditional on these five playbooks and constructed scenarios; no population inference.',
        'percentile_95_intervals': {k: [percentile(v, .025), percentile(v, .975)] if v else None
                                  for k, v in values.items()}}


def run(collection, output):
    assert not output.exists(), 'Preserve any earlier analysis output'
    lock = (collection / 'runtime/queue.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    p = c.amendment_check(collection)
    acceptance = json.loads((collection / 'collection-acceptance.json').read_text())
    progress = json.loads((collection / 'runtime/progress.json').read_text())
    assert progress['state'] == 'complete' and acceptance['accepted']
    assert len(acceptance['records']) == len(p['order']) == 295
    assert acceptance['records'] == progress['completed']
    assert acceptance['protocol_sha256'] == p['protocol_sha256']
    cleanup = json.loads((collection / 'runtime/cleanup-and-retention.json').read_text())
    assert all(not x['running'] for x in cleanup['after'].values())
    assert cleanup['deleted_images'] == cleanup['deleted_volumes'] == []
    pointer = json.loads((collection / 'runtime/current-operational-recovery.json').read_text())
    receipt_path = ROOT / pointer['path']
    assert c.original.sha(receipt_path) == pointer['sha256']
    recovery = json.loads(receipt_path.read_text())
    script_path = collection / 'runtime/resume-reset-211.py'
    assert c.original.sha(script_path) == recovery['operational_source_sha256']
    spec = importlib.util.spec_from_file_location('recovery211', script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.guard_prior(recovery['prior_recovery_pointer'])
    for item in recovery['accepted_prefix']:
        directory = collection / 'trials' / item['trial_id']
        assert c.original.sha(directory / 'record.json') == item['record_sha256']
        assert c.original.sha(directory / 'audit.json') == item['audit_sha256']
    model = json.loads((ROOT / p['comparison_model']).read_text())['model']
    references = json.loads((ROOT / p['whole_environment_references']).read_text())
    definitions = {x['id']: x for x in p['scenarios'] + p['controls']}
    rows, identities, blind_sources, ablations = [], [], [], []
    for index, entry in enumerate(p['order']):
        assert acceptance['records'][index]['entry'] == entry
        directory = collection / 'trials' / entry['trial_id']
        record = json.loads((directory / 'record.json').read_text())
        saved = json.loads((directory / 'audit.json').read_text())
        case = copy.deepcopy(p)
        case['trial_identity'] = entry
        checked = c.BASE_AUDIT(record, case, directory)
        if 'collector_amendment_sha256' in saved:
            assert record['collector_amendment_sha256'] == p['collector_amendment_sha256']
            checked['collector_amendment_sha256'] = p['collector_amendment_sha256']
        assert checked == saved and saved == acceptance['records'][index]['audit'] and saved['accepted']
        for sample in record['samples']:
            historic = sample.get('historical_business_check', {})
            if historic.get('historical_subject_absent'):
                assert record['candidate'] == 'P3' and historic['healthy'] is False
                assert historic['rollout']['complete'] is False and historic['rollout']['subject_present'] is False
        definition = definitions[entry['scenario']]
        row = {'trial_id': entry['trial_id'], 'scenario': entry['scenario'],
            'candidate': record['candidate'], 'condition': definition.get('condition'),
            'repeat': entry['repeat'], 'category': entry['category'], 'arm': entry['arm'],
            'outcome': record['repair_outcome'], 'action_executed': record['action_executed'],
            'execution_complete': record['execution_complete'], 'target_recovery': record['target_recovery'],
            'non_target_regression': record['non_target_regression'],
            'duration_seconds': record['completed_at'] - record['started_at'],
            'window_seconds': record['window_seconds'], 'decisions': {}}
        identity = {'trial_id': entry['trial_id'], 'record_sha256': c.original.sha(directory / 'record.json'),
                    'audit_sha256': c.original.sha(directory / 'audit.json')}
        if entry['arm'] == 'repair':
            observed = record['pre_action_policy_observation']
            initial = record['initial_method_decisions']
            current = assessor.contract(definition, p['protocol_path'], p['protocol_sha256'])
            states = supported.compare(record['candidate'], p['candidate_hashes'][record['candidate']],
                                       observed, current, now=initial['at'], ttl=p['decision_configuration']['observation_ttl'])
            assert states == initial['proposed']['states']
            recomputed = assessor.decision(states)
            assert {k: v for k, v in initial['proposed'].items() if k != 'states'} == recomputed
            assert policies.fault_matching(model, record['candidate'], initial['incident']) == initial['fault_matching']
            reference = references[record['candidate']]
            assert policies.whole_environment(reference['snapshot'], methods.scope_snapshot(observed),
                reference['criteria'], methods.criteria(definition, p)) == initial['whole_environment']
            assert initial['at'] < record['action_started_at']
            row['decisions'] = {method: initial[method]['decision'] for method in METHODS}
            for omitted in CONDITIONS:
                predicted = assessor.decision([s for s in states if s['condition'] != omitted])
                assert predicted == initial['ablations'][omitted]
                if entry['category'] == 'main':
                    ablations.append({'trial_id': entry['trial_id'], 'scenario': entry['scenario'],
                        'candidate': record['candidate'], 'omitted': omitted,
                        'full_decision': initial['proposed']['decision'], 'ablated_decision': predicted['decision'],
                        'counterfactual_outcome': record['repair_outcome']})
            review_path = directory / 'blind-evidence-review-input.json'
            review = json.loads(review_path.read_text())
            assert review['no_outcome_or_expected_effect_included'] is True
            identity['blind_input_sha256'] = c.original.sha(review_path)
            blind_sources.append({'source': str(review_path.relative_to(ROOT)), 'trial_id': entry['trial_id'],
                                  'category': entry['category'], 'sha256': identity['blind_input_sha256']})
        else:
            assert record['no_repair_persistence_confirmed'] and record['action_executed'] is False
        rows.append(row)
        identities.append(identity)
    assert len({x['trial_id'] for x in rows}) == 295
    assert dict(collections.Counter(x['category'] for x in rows)) == p['counts']
    ledger = json.loads((collection / 'invalid-attempts.json').read_text())
    rejected = []
    for attempt in ledger['attempts']:
        archive = ROOT / attempt['archive']
        assert c.tree_hashes(archive) == attempt['file_hashes']
        record = json.loads((archive / 'record.json').read_text())
        rejected.append({'trial_id': attempt['entry']['trial_id'], 'category': attempt['category'],
            'action_executed': record['action_executed'], 'outcome': record['repair_outcome'],
            'post_reset_confirmed': record.get('post_reset_confirmed'), 'archive': attempt['archive'],
            'record_sha256': c.original.sha(archive / 'record.json')})
        if record['action_executed']:
            assert attempt['retain_in_actual_execution_attempts_and_indeterminate_rate']
            assert record['repair_outcome'] == 'Indeterminate'
            review_path = archive / 'blind-evidence-review-input.json'
            blind_sources.append({'source': str(review_path.relative_to(ROOT)),
                'trial_id': attempt['entry']['trial_id'] + '-rejected-attempt-1',
                'category': 'additional-executed-main-attempt', 'sha256': c.original.sha(review_path)})
    main = [x for x in rows if x['category'] == 'main']
    assert len(main) == 200 and len(groups(main, 'scenario')) == 40
    assert all(len(x) == 5 for x in groups(main, 'scenario').values())
    extra_executed = [x for x in rejected if x['action_executed']]
    all_executed = len(main) + len(extra_executed)
    successes = sum(x['outcome'] == 'Successful repair' for x in main)
    failures = sum(x['outcome'] == 'Confirmed failure' for x in main)
    summary = {'scope': 'RQ1 completed counterfactual replays and saved initial RQ3a decisions only.',
        'completion_at': acceptance['at'], 'main': tally(main),
        'by_playbook': {k: tally(v) for k, v in sorted(groups(main, 'candidate').items())},
        'by_primary_condition': {k: tally(v) for k, v in sorted(groups(main, 'condition').items())},
        'by_scenario': {k: tally(v) for k, v in sorted(groups(main, 'scenario').items())},
        'controls': {k: tally(v) for k, v in sorted(groups([x for x in rows if x['category'] != 'main'], 'category').items())},
        'all_actual_main_execution_attempts': {'executed': all_executed,
            'determinate': len(main), 'indeterminate': len(extra_executed),
            'successful': successes, 'confirmed_failures': failures,
            'determinate_fraction': len(main) / all_executed,
            'success_fraction_determinate': successes / len(main),
            'success_bounds_all_attempts': [successes / all_executed, (successes + len(extra_executed)) / all_executed],
            'failure_bounds_all_attempts': [failures / all_executed, (failures + len(extra_executed)) / all_executed]},
        'rejected_attempts': rejected, 'total_collection_attempts': len(rows) + len(rejected),
        'setup_rejections_without_candidate_action': sum(not x['action_executed'] for x in rejected),
        'initial_decision_comparison': {method: decisions_table(main, method) for method in METHODS},
        'ablations': {condition: {'computations': len([x for x in ablations if x['omitted'] == condition]),
            'decision_changes': sum(x['full_decision'] != x['ablated_decision'] for x in ablations if x['omitted'] == condition),
            'decisions': dict(collections.Counter(x['ablated_decision'] for x in ablations if x['omitted'] == condition))}
            for condition in CONDITIONS},
        'durations': {'mean_seconds': statistics.mean(x['duration_seconds'] for x in rows),
            'median_seconds': statistics.median(x['duration_seconds'] for x in rows),
            'accepted_active_hours': sum(x['duration_seconds'] for x in rows) / 3600,
            'elapsed_collection_hours': (max(json.loads((collection / 'trials' / x['trial_id'] / 'record.json').read_text())['completed_at'] for x in rows)
                - min(json.loads((collection / 'trials' / x['trial_id'] / 'record.json').read_text())['started_at'] for x in rows)) / 3600,
            'full_window_min_seconds': min(x['window_seconds'] for x in rows),
            'full_window_max_seconds': max(x['window_seconds'] for x in rows)},
        'uncertainty': bootstrap(main),
        'limitations': ['Five playbooks in one application; constructed scenarios are not a random population sample.',
            'Five repeated original-condition repair controls per playbook are shared by eight variants, not 200 independent controls.',
            'Failure components overlap; do not add their counts as mutually exclusive outcomes.',
            'Saved initial decisions and their ablations are diagnostic comparisons, not actual subsequent response routes.',
            'Independent outcome-blinded evidence review and actual RQ3a response trials remain pending.',
            'RQ3b recurring sequences and comparative recovery-time results are not collected.']}
    output.mkdir()
    (output / 'tool-snapshot').mkdir()
    (output / 'tool-snapshot' / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    save(output / 'input-identities.json', {'protocol_sha256': p['protocol_sha256'],
        'collector_amendment_sha256': p['collector_amendment_sha256'],
        'operational_recovery_sha256': pointer['sha256'], 'accepted_records': identities,
        'collection_acceptance_sha256': c.original.sha(collection / 'collection-acceptance.json'),
        'invalid_attempts_sha256': c.original.sha(collection / 'invalid-attempts.json')})
    save(output / 'analysis.json', summary)
    save(output / 'trial-level-data.json', rows)
    save(output / 'ablation-decisions.json', ablations)
    # Freeze evidence-only manipulations before computing their outputs.
    config = {'at': time.time(), 'type': 'offline evidence-only rule verification',
        'sources': 'Five accepted supplementary original-condition registration observations',
        'missing_keys': ['deployments', 'services', 'replicasets', 'pods', 'endpointslices'],
        'stale_ages_seconds': [31, 60, 120, 300, 900], 'observation_ttl_seconds': 30,
        'expected_computations': 50, 'no_physical_mutation_or_repair_trial': True,
        'source_selection_independent_of_main_outcomes': True}
    save(output / 'evidence-only-protocol.json', config)
    evidence_cases = []
    for candidate in sorted(references):
        source = ROOT / references[candidate]['source']['record']
        source_record = json.loads(source.read_text())
        definition = next(x for x in p['controls'] if x['id'] == candidate + '-original-conditions')
        current = assessor.contract(definition, p['protocol_path'], p['protocol_sha256'])
        observation = source_record['pre_action_policy_observation']
        for key in config['missing_keys']:
            changed = copy.deepcopy(observation)
            changed.pop(key)
            states = supported.compare(candidate, p['candidate_hashes'][candidate], changed, current,
                now=source_record['initial_method_decisions']['at'], ttl=30)
            evidence_cases.append({'candidate': candidate, 'operation': 'missing_' + key,
                'source_sha256': c.original.sha(source), 'states': states, **assessor.decision(states)})
        for age in config['stale_ages_seconds']:
            states = supported.compare(candidate, p['candidate_hashes'][candidate], observation, current,
                now=observation['started_at'] + age, ttl=30)
            evidence_cases.append({'candidate': candidate, 'operation': 'stale_age_' + str(age),
                'source_sha256': c.original.sha(source), 'states': states, **assessor.decision(states)})
    assert len(evidence_cases) == 50 and all(x['decision'] == 'Revalidate' for x in evidence_cases)
    save(output / 'evidence-only-results.json', {'computations': evidence_cases,
        'scope': 'Missing/stale observations produce unresolved states. No actual refresh or isolated execution was performed.'})
    review = output / 'blind-review'
    (review / 'packets').mkdir(parents=True)
    random.Random(20261006).shuffle(blind_sources)
    key = []
    for index, source in enumerate(blind_sources, 1):
        original = json.loads((ROOT / source['source']).read_text())
        packet = {name: original[name] for name in ['candidate', 'candidate_sha256', 'current_requirements', 'observations']}
        packet['review_instructions'] = 'Classify each requirement as Supported, Contradicted or Unresolved using only candidate code, registered requirements and these pre-action observations. Do not access collection records, analysis outputs, initial decisions or outcomes.'
        packet['independent_review_completed'] = False
        packet['requirements_to_review'] = list(CONDITIONS)
        name = f'review-{index:03d}.json'
        save(review / 'packets' / name, packet)
        key.append(dict(source, packet=name, packet_sha256=c.original.sha(review / 'packets' / name)))
    save(output / 'blind-review-linkage.json', {'restricted_to_analysis_after_independent_review': True, 'mapping': key})
    save(review / 'review-status.json', {'packets': len(key), 'completed': 0,
        'machine_states_and_initial_decisions_removed': True, 'outcomes_not_in_packets': True,
        'scope': '200 main,50 repaired controls,and1 additional executed Indeterminate main attempt. Not a completed blind review.'})
    audit = {'at': time.time(), 'accepted': True, 'accepted_records_reaudited': len(rows),
        'main': len(main), 'controls': len(rows) - len(main), 'scenarios': len(groups(main, 'scenario')),
        'repeats_per_scenario': 5, 'invalid_attempts_preserved': len(rejected),
        'additional_executed_indeterminate_attempts': len(extra_executed),
        'saved_initial_decisions_recomputed': len([x for x in rows if x['arm'] == 'repair']) * 3,
        'main_ablation_computations_recomputed': len(ablations), 'evidence_only_computations': 50,
        'blind_review_packets_prepared_not_reviewed': len(key), 'no_raw_collection_file_rewritten': True,
        'no_threshold_rule_or_outcome_retuned': True, 'final_reset_and_container_retention_confirmed': True}
    save(output / 'final-audit.json', audit)
    identities_out = {str(x.relative_to(output)): c.original.sha(x) for x in sorted(output.rglob('*')) if x.is_file()}
    save(output / 'analysis-freeze.json', {'at': time.time(), 'files': identities_out,
        'analysis_script_sha256': c.original.sha(Path(__file__)), 'immutable_outputs': True})
    print(json.dumps({'audit': audit, 'main': summary['main'], 'controls': summary['controls'],
        'by_playbook': summary['by_playbook'], 'initial_decisions': summary['initial_decision_comparison'],
        'ablations': summary['ablations'], 'uncertainty': summary['uncertainty']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--collection', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.collection.resolve(), args.output.resolve())

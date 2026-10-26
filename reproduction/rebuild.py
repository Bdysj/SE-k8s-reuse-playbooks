"""Rebuild RQ1--RQ3 tables from frozen evidence. No cluster or model access."""
import argparse
from collections import Counter, defaultdict
import copy
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'reproduction'))
import rq1_metrics as metrics

METHODS = ('proposed', 'fault_matching', 'whole_environment')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def csv_file(path, rows):
    if not rows:
        return
    with path.open('w', newline='') as stream:
        fields = list(dict.fromkeys(k for row in rows for k in row))
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})


class Reader:
    def __init__(self, root):
        self.root = root.resolve()
        self.identities = {}

    def path(self, name):
        path = (self.root / name).resolve()
        require(path.is_relative_to(self.root), 'Input escapes evidence root: ' + str(name))
        require(path.is_file(), 'Missing evidence: ' + str(name))
        return path

    def check(self, name, expected):
        path = self.path(name)
        actual = digest(path)
        require(actual == expected, 'Evidence identity mismatch: ' + str(name))
        self.identities[str(path.relative_to(self.root))] = actual

    def read(self, name):
        path = self.path(name)
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        key = str(path.relative_to(self.root))
        require(key not in self.identities or self.identities[key] == actual, 'Input changed: ' + key)
        self.identities[key] = actual
        return json.loads(raw)

    def stable(self):
        for name, identity in self.identities.items():
            require(digest(self.root / name) == identity, 'Input changed during reconstruction: ' + name)


def compare(actual, expected, path='result'):
    """Compare every leaf; only timestamps explicitly removed by the caller differ."""
    if isinstance(actual, dict) and isinstance(expected, dict):
        require(set(actual) == set(expected), 'Different keys at ' + path)
        return sum(compare(actual[k], expected[k], path + '.' + str(k)) for k in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        require(len(actual) == len(expected), 'Different list length at ' + path)
        return sum(compare(a, b, path + '[' + str(i) + ']') for i, (a, b) in enumerate(zip(actual, expected)))
    if isinstance(actual, float) or isinstance(expected, float):
        require(isinstance(actual, (int, float)) and isinstance(expected, (int, float))
                and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9), 'Numeric difference at ' + path)
    else:
        require(actual == expected, 'Value difference at ' + path)
    return 1


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def guard_external_actions(output):
    """Prevent subprocesses, network connections and writes outside the new output."""
    def resolve_at(value, directory_fd=None):
        path = Path(os.fsdecode(value))
        if path.is_absolute() or directory_fd in (None, -1):
            return path.resolve()
        # shutil uses directory-relative unlink/rmdir for safe temporary cleanup.
        # Accept a directory FD only when its inode belongs to this new output.
        stat = os.fstat(directory_fd)
        for directory in [output, *(p for p in output.rglob('*') if p.is_dir())]:
            known = directory.stat()
            if (known.st_dev, known.st_ino) == (stat.st_dev, stat.st_ino):
                return (directory / path).resolve()
        raise ValueError('Write uses a directory outside the new output')

    def audit(event, args):
        if event in {'subprocess.Popen', 'os.system', 'socket.connect', 'socket.bind'}:
            raise RuntimeError('Offline reconstruction prohibits ' + event)
        candidates = []
        if event == 'open':
            path, mode, flags = args
            writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing and isinstance(path, (str, bytes, os.PathLike)):
                candidates.append((path, None))
        elif event == 'os.mkdir':
            candidates.append((args[0], args[2]))
        elif event in {'os.remove', 'os.rmdir'}:
            candidates.append((args[0], args[1]))
        elif event == 'os.symlink':
            candidates.append((args[1], args[2]))
        elif event in {'os.rename', 'os.link'}:
            candidates.extend(((args[0], args[2]), (args[1], args[3])))
        for value, directory_fd in candidates:
            path = resolve_at(value, directory_fd)
            require(path.is_relative_to(output), 'Offline write outside output directory: ' + str(path))
    sys.addaudithook(audit)


def rq1(reader, output):
    import reuse_assessor as assessor
    import supported_reuse_v2 as supported
    import comparison_policies as policies
    import formal_methods as methods
    import reset_baseline as baseline

    collection = Path('results/formal-main-v1')
    analysis_dir = Path('results/formal-main-analysis-v1')
    identities = reader.read(analysis_dir / 'input-identities.json')
    reader.check(collection / 'protocol.json', identities['protocol_sha256'])
    protocol = reader.read(collection / 'protocol.json')
    protocol.update(protocol_path=str(collection / 'protocol.json'), protocol_sha256=identities['protocol_sha256'])
    baseline.MANIFEST = reader.root / protocol['manifest']
    baseline.FINGERPRINT = baseline.MANIFEST.with_suffix('.fingerprint.json')
    baseline.NS, baseline.CONTEXT = protocol['namespace'], protocol['context']
    for name in ('collection-acceptance.json', 'invalid-attempts.json'):
        reader.check(collection / name, identities['collection_acceptance_sha256' if name.startswith('collection') else 'invalid_attempts_sha256'])
    acceptance = reader.read(collection / 'collection-acceptance.json')
    require(acceptance['accepted'] is True and len(acceptance['records']) == 295, 'RQ1 acceptance required')
    frozen = reader.read(analysis_dir / 'analysis-freeze.json')
    for name, identity in frozen['files'].items():
        reader.check(analysis_dir / name, identity)
    model = reader.read(protocol['comparison_model'])['model']
    references = reader.read(protocol['whole_environment_references'])
    definitions = {r['id']: r for r in protocol['scenarios'] + protocol['controls']}
    rows, raw_records, ablations, attempt_rows = [], [], [], []
    require(len(protocol['order']) == len(identities['accepted_records']) == 295, 'RQ1 queue size')
    for entry, identity, accepted in zip(protocol['order'], identities['accepted_records'], acceptance['records']):
        require(entry == accepted['entry'] and entry['trial_id'] == identity['trial_id'], 'RQ1 trial ordering')
        directory = collection / 'trials' / entry['trial_id']
        reader.check(directory / 'record.json', identity['record_sha256'])
        reader.check(directory / 'audit.json', identity['audit_sha256'])
        record = reader.read(directory / 'record.json')
        audit = reader.read(directory / 'audit.json')
        require(audit == accepted['audit'] and audit['accepted'] is True, 'RQ1 record acceptance')
        require(record['trial_identity'] == entry, 'RQ1 raw trial identity')
        definition = definitions[entry['scenario']]
        row = {'trial_id': entry['trial_id'], 'scenario': entry['scenario'], 'candidate': record['candidate'],
               'condition': definition.get('condition'), 'repeat': entry['repeat'], 'category': entry['category'],
               'arm': entry['arm'], 'outcome': record['repair_outcome'], 'action_executed': record['action_executed'],
               'execution_complete': record['execution_complete'], 'target_recovery': record['target_recovery'],
               'non_target_regression': record['non_target_regression'],
               'duration_seconds': record['completed_at'] - record['started_at'],
               'window_seconds': record['window_seconds'], 'decisions': {}}
        if entry['arm'] == 'repair':
            initial = record['initial_method_decisions']
            contract = assessor.contract(definition, protocol['protocol_path'], protocol['protocol_sha256'])
            states = supported.compare(record['candidate'], protocol['candidate_hashes'][record['candidate']],
                record['pre_action_policy_observation'], contract, now=initial['at'],
                ttl=protocol['decision_configuration']['observation_ttl'])
            compare(states, initial['proposed']['states'], entry['trial_id'] + '.states')
            compare(assessor.decision(states), {k: v for k, v in initial['proposed'].items() if k != 'states'})
            compare(policies.fault_matching(model, record['candidate'], initial['incident']), initial['fault_matching'])
            reference = references[record['candidate']]
            compare(policies.whole_environment(reference['snapshot'], methods.scope_snapshot(record['pre_action_policy_observation']),
                reference['criteria'], methods.criteria(definition, protocol)), initial['whole_environment'])
            row['decisions'] = {method: initial[method]['decision'] for method in METHODS}
            for omitted in assessor.CONDITIONS:
                decision = assessor.decision([s for s in states if s['condition'] != omitted])
                compare(decision, initial['ablations'][omitted])
                if entry['category'] == 'main':
                    ablations.append({'trial_id': entry['trial_id'], 'scenario': entry['scenario'],
                        'candidate': record['candidate'], 'omitted': omitted,
                        'full_decision': initial['proposed']['decision'], 'ablated_decision': decision['decision'],
                        'counterfactual_outcome': record['repair_outcome']})
        rows.append(row)
        raw_records.append(record)
        attempt_rows.append({**entry, 'record': str(directory / 'record.json'), 'included': True,
                             'action_executed': record['action_executed'], 'outcome': record['repair_outcome']})
    compare(rows, reader.read(analysis_dir / 'trial-level-data.json'), 'RQ1.trial_rows')
    compare(ablations, reader.read(analysis_dir / 'ablation-decisions.json'), 'RQ1.ablations')
    rejected = []
    for attempt in reader.read(collection / 'invalid-attempts.json')['attempts']:
        archive = Path(attempt['archive'])
        for name, identity in attempt['file_hashes'].items():
            reader.check(archive / name, identity)
        record = reader.read(archive / 'record.json')
        rejected.append({'trial_id': attempt['entry']['trial_id'], 'category': attempt['category'],
            'action_executed': record['action_executed'], 'outcome': record['repair_outcome'],
            'post_reset_confirmed': record.get('post_reset_confirmed'), 'archive': str(archive),
            'record_sha256': digest(reader.path(archive / 'record.json'))})
        attempt_rows.append({**attempt['entry'], 'record': str(archive / 'record.json'), 'included': False,
                             'action_executed': record['action_executed'], 'outcome': record['repair_outcome']})
    main = [r for r in rows if r['category'] == 'main']
    extra = [r for r in rejected if r['action_executed']]
    require(len(main) == 200 and len(rows) == 295 and len(rejected) == 3, 'RQ1 complete collection required')
    success = sum(r['outcome'] == 'Successful repair' for r in main)
    failed = sum(r['outcome'] == 'Confirmed failure' for r in main)
    n = len(main) + len(extra)
    result = {
        'main': metrics.tally(main),
        'by_playbook': {k: metrics.tally(v) for k, v in sorted(metrics.groups(main, 'candidate').items())},
        'by_primary_condition': {k: metrics.tally(v) for k, v in sorted(metrics.groups(main, 'condition').items())},
        'by_scenario': {k: metrics.tally(v) for k, v in sorted(metrics.groups(main, 'scenario').items())},
        'controls': {k: metrics.tally(v) for k, v in sorted(metrics.groups([r for r in rows if r['category'] != 'main'], 'category').items())},
        'all_actual_main_execution_attempts': {'executed': n, 'determinate': len(main), 'indeterminate': len(extra),
            'successful': success, 'confirmed_failures': failed, 'determinate_fraction': len(main) / n,
            'success_fraction_determinate': success / len(main),
            'success_bounds_all_attempts': [success / n, (success + len(extra)) / n],
            'failure_bounds_all_attempts': [failed / n, (failed + len(extra)) / n]},
        'rejected_attempts': rejected, 'total_collection_attempts': len(rows) + len(rejected),
        'setup_rejections_without_candidate_action': sum(not r['action_executed'] for r in rejected),
        'initial_decision_comparison': {method: metrics.decisions_table(main, method) for method in METHODS},
        'ablations': {condition: {'computations': sum(r['omitted'] == condition for r in ablations),
            'decision_changes': sum(r['full_decision'] != r['ablated_decision'] for r in ablations if r['omitted'] == condition),
            'decisions': dict(Counter(r['ablated_decision'] for r in ablations if r['omitted'] == condition))}
            for condition in assessor.CONDITIONS},
        'durations': {'mean_seconds': statistics.mean(r['duration_seconds'] for r in rows),
            'median_seconds': statistics.median(r['duration_seconds'] for r in rows),
            'accepted_active_hours': sum(r['duration_seconds'] for r in rows) / 3600,
            'elapsed_collection_hours': (max(r['completed_at'] for r in raw_records) - min(r['started_at'] for r in raw_records)) / 3600,
            'full_window_min_seconds': min(r['window_seconds'] for r in rows),
            'full_window_max_seconds': max(r['window_seconds'] for r in rows)},
        'uncertainty': metrics.bootstrap(main)}
    expected = reader.read(analysis_dir / 'analysis.json')
    leaves = compare(result, {k: expected[k] for k in result}, 'RQ1.analysis')
    config = reader.read(analysis_dir / 'evidence-only-protocol.json')
    evidence_cases = []
    for candidate in sorted(references):
        name = references[candidate]['source']['record']
        record = reader.read(name)
        definition = next(r for r in protocol['controls'] if r['id'] == candidate + '-original-conditions')
        contract = assessor.contract(definition, protocol['protocol_path'], protocol['protocol_sha256'])
        observation = record['pre_action_policy_observation']
        for key in config['missing_keys']:
            changed = copy.deepcopy(observation)
            changed.pop(key)
            states = supported.compare(candidate, protocol['candidate_hashes'][candidate], changed, contract,
                                       now=record['initial_method_decisions']['at'], ttl=30)
            evidence_cases.append({'candidate': candidate, 'operation': 'missing_' + key,
                'source_sha256': digest(reader.path(name)), 'states': states, **assessor.decision(states)})
        for age in config['stale_ages_seconds']:
            states = supported.compare(candidate, protocol['candidate_hashes'][candidate], observation, contract,
                                       now=observation['started_at'] + age, ttl=30)
            evidence_cases.append({'candidate': candidate, 'operation': 'stale_age_' + str(age),
                'source_sha256': digest(reader.path(name)), 'states': states, **assessor.decision(states)})
    compare(evidence_cases, reader.read(analysis_dir / 'evidence-only-results.json')['computations'], 'RQ2.evidence_cases')
    save(output / 'rq1-analysis.json', result)
    save(output / 'rq2-evidence-only.json', evidence_cases)
    csv_file(output / 'rq1-trials.csv', rows)
    csv_file(output / 'rq1-all-attempts.csv', attempt_rows)
    csv_file(output / 'rq1-by-playbook.csv', [{'playbook': k, **v} for k, v in result['by_playbook'].items()])
    return {'accepted_records': len(rows), 'main': len(main), 'controls': len(rows) - len(main),
            'unique_attempts': len(attempt_rows), 'policy_decisions_recomputed': 750,
            'ablations_recomputed': len(ablations), 'evidence_only_cases_recomputed': len(evidence_cases),
            'analysis_leaves_matched': leaves}


def rq3a(reader, output):
    module = load_module('archived_rq3a_analysis', ROOT / 'offline_work/rq3a_analysis_v3/analysis.py')
    snapshot = module.collect_snapshot(reader.root, reader.root / 'results/formal-rq3a-responses-v10', final=True)
    result = module.build(snapshot, final=True)
    expected_dir = Path('results/offline-parallel-v1/rq3a/final-001-complete240')
    leaves = compare(result, reader.read(expected_dir / 'analysis.json'), 'RQ3a.analysis')
    expected_snapshot = reader.read(expected_dir / 'input-snapshot.json')
    for name in ('rows', 'extra_rows', 'rejected_entries', 'deduplicated_rejections', 'input_sha256'):
        compare(snapshot[name], expected_snapshot[name], 'RQ3a.snapshot.' + name)
    for name, identity in snapshot['input_sha256'].items():
        reader.check(name, identity)
    save(output / 'rq3a-analysis.json', result)
    csv_file(output / 'rq3a-all-attempts.csv', [{**r, 'included': included} for included, rows in
        ((True, snapshot['rows']), (False, snapshot['extra_rows'])) for r in rows])
    cross, nonexec = Counter(), Counter()
    for row in snapshot['rows']:
        key = (row['method'], row['initial_decision'], row['isolated_outcome'] if row['isolated_executed'] else 'Not executed', row['outcome'])
        cross[key] += 1
        if not row['executed']:
            nonexec[row['method']] += 1
    csv_file(output / 'rq3a-routes.csv', [dict(zip(('method', 'initial_decision', 'isolated_outcome', 'live_outcome'), key), n=value)
             for key, value in sorted(cross.items(), key=lambda x: str(x[0]))])
    save(output / 'rq3a-input-snapshot.json', snapshot)
    return {'accepted_responses': len(snapshot['rows']), 'extra_attempts': len(snapshot['extra_rows']),
            'matched_blocks': result['matched_comparisons']['complete_matched_blocks'], 'analysis_leaves_matched': leaves,
            'nonexecution_by_method': dict(nonexec)}


def rq3b(reader, output):
    module = load_module('archived_rq3b_analysis', ROOT / 'reproduction/rq3b_analysis.py')
    module.ROOT = reader.root
    module.LIVE = reader.root / 'results/formal-rq3b-short-v5'
    module.OUT = output / 'rq3b'
    module.OUT.mkdir()
    with __import__('contextlib').redirect_stdout(io.StringIO()):
        module.main()
    result = json.loads((module.OUT / 'analysis.json').read_text())
    expected = reader.read('results/rq3b-final-document-v1/analysis.json')
    leaves = compare({k: v for k, v in result.items() if k != 'at'},
                     {k: v for k, v in expected.items() if k != 'at'}, 'RQ3b.analysis')
    snapshot = json.loads((module.OUT / 'input-snapshot.json').read_text())
    for name, identity in snapshot['input_sha256'].items():
        reader.check(name, identity)
    incidents = []
    for sequence in result['sequences']:
        for i in range(1, 6):
            name = str(Path(sequence['evidence_directory']) / f'incident-{i:02d}/response.json')
            record = reader.read(name)
            phases = defaultdict(float)
            for event in record['events']:
                phases[event['stage']] += event['completed_at'] - event['started_at']
            incidents.append({'sequence': sequence['name'], 'template': sequence['template'], 'process': sequence['process'],
                'incident': i, 'record': name, 'recovered': record['recovered'], 'censored': record['censored'],
                'seconds': record['capped_recovery_seconds'], 'phase_seconds': dict(phases)})
    csv_file(output / 'rq3b-sequences.csv', result['sequences'])
    csv_file(output / 'rq3b-incidents.csv', incidents)
    return {'sequences': len(result['sequences']), 'formal_incidents': len(incidents), 'analysis_leaves_matched': leaves,
            'model_calls': 0, 'archived_model_calls_verified': sum(r['model_calls'] for r in result['sequences'])}


def conformance(output):
    temporary = output / 'temporary-test-files'
    temporary.mkdir()
    tempfile.tempdir = str(temporary)
    modules = ['test_reuse_assessor', 'test_reuse_workflow', 'test_rq3a_response_workflow', 'test_rq3a_launch_audit_v3']
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(m) for m in modules)
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    (output / 'offline-conformance.log').write_text(log.getvalue())
    require(result.wasSuccessful() and result.testsRun == 44, 'Frozen offline conformance checks failed; see output log')
    return {'tests': result.testsRun, 'passed': True, 'new_live_trials': 0, 'new_model_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    require(__debug__, 'Run Python without -O; frozen analyzers use assertions')
    output = (args.output or ROOT / 'outputs' / ('tables-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))).resolve()
    require(output.is_relative_to(ROOT / 'outputs'), 'Choose a new directory under outputs/')
    require(not output.exists(), 'Preserve earlier output; choose a new directory')
    require((args.data_root / 'results/formal-main-v1/protocol.json').is_file(),
            'Evidence is missing. Restore release evidence assets into the repository root. See README.md.')
    output.mkdir(parents=True)
    guard_external_actions(output)
    reader = Reader(args.data_root)
    try:
        adapters = json.loads((ROOT / 'reproduction/analysis-sources.json').read_text())
        for item in adapters:
            require(digest(ROOT / item['release_path']) == item['sha256'],
                    'Release analysis source changed: ' + item['release_path'])
        frozen = json.loads((ROOT / 'reproduction/frozen-inputs.json').read_text())
        for name, identity in frozen.items():
            reader.check(name, identity)
        print('Frozen inputs verified: ' + str(len(frozen)), flush=True)
        checks = {'RQ1_RQ2': rq1(reader, output)}
        print('RQ1/RQ2 tables match the archived analysis.', flush=True)
        checks['RQ3a'] = rq3a(reader, output)
        print('RQ3a tables match the archived analysis.', flush=True)
        checks['RQ3b'] = rq3b(reader, output)
        print('RQ3b tables match the archived analysis.', flush=True)
        checks['offline_conformance'] = conformance(output)
        reader.stable()
        save(output / 'verified-inputs.json', reader.identities)
        save(output / 'acceptance.json', {'accepted': True, 'checks': checks, 'frozen_inputs_verified': len(frozen),
            'release_analysis_sources_verified': len(adapters),
            'unique_inputs_verified': len(reader.identities), 'input_stability_verified': True,
            'network_and_subprocesses_blocked': True, 'writes_limited_to_new_output': True,
            'new_live_trials': 0, 'new_model_calls': 0,
            'numeric_tolerance': {'relative': 1e-12, 'absolute': 1e-9},
            'excluded_from_equality': ['New analysis timestamps; narrative scope/limitations of RQ1 are not numerical tables'],
            'output_files_sha256': {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob('*')) if p.is_file()}})
        print('PASS: ' + str(output), flush=True)
    except Exception as error:
        save(output / 'failure.json', {'accepted': False, 'error_type': type(error).__name__, 'message': str(error)})
        raise


if __name__ == '__main__':
    main()

"""Descriptive analysis of the audited nine registered RQ3b sequences."""
from pathlib import Path
import collections
import hashlib
import json
import statistics
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
LIVE = ROOT / 'results/formal-rq3b-short-v5'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(path):
    return json.loads(path.read_text())

def main():
    assert not (OUT / 'analysis.json').exists(), 'Preserve prior analysis.'
    audit = load(LIVE / 'completion-audit-v1.json')
    assert audit['passed'] and audit['counts']['formal_records'] == 45
    manifest = load(LIVE / 'completion-evidence-manifest-v1.json')
    for name, sha in manifest.items():
        assert digest(ROOT / name) == sha, name
    protocol = load(LIVE / 'protocol.json')
    assert digest(LIVE / 'protocol.json') == load(LIVE / 'freeze.json')['protocol_sha256']
    carried = {s['sequence_id']: s for s in load(LIVE / 'retained-sequences.json')['sequences']}
    rows = []
    for item in protocol['order']:
        name = item['name']
        directory = ROOT / carried[name]['_evidence_directory'] if name in carried else LIVE / name
        sequence = load(directory / 'sequence.json')
        records = sequence['records']
        assert len(records) == 5 and sequence['formal_trials'] == 5
        assert all(r == load(directory / f'incident-{i:02d}' / 'response.json') for i, r in enumerate(records, 1))
        times = [r['capped_recovery_seconds'] for r in records]
        assert all(r['recovered'] and not r['censored'] for r in records)
        assert abs(sum(times) - sequence['capped_cumulative_seconds']) < 1e-9
        phases = collections.defaultdict(float)
        for record in records:
            for event in record['events']:
                phases[event['stage']] += event['completed_at'] - event['started_at']
        calls = sorted(directory.glob('incident-*/planning-*/bounded-call.json'))
        usage = collections.Counter()
        api_seconds = []
        for call in calls:
            receipt = load(call)
            assert receipt['accepted']
            api_seconds.append(receipt['elapsed_seconds'])
            usage.update({key: receipt['usage'][key] for key in ('input_tokens', 'output_tokens', 'total_tokens')})
        assert len(calls) == sum(r['generated_attempts'] for r in records)
        live_attempts = [a for r in records for a in r['live_attempts'] if a['executed']]
        isolated_attempts = [a for r in records for a in r['isolated_attempts'] if a['executed']]
        rows.append({
            **item, 'evidence_directory': str(directory.relative_to(ROOT)),
            'incident_recovery_seconds': times,
            'cumulative_seconds': sum(times),
            'cumulative_by_incident_seconds': [sum(times[:i]) for i in range(1, 6)],
            'median_seconds': statistics.median(times), 'min_seconds': min(times), 'max_seconds': max(times),
            'live_executions': len(live_attempts), 'successful_live_executions': sum(a['target_recovered'] for a in live_attempts),
            'invalid_live_executions': sum(a['outcome'] == 'Confirmed failure' for a in live_attempts),
            'incidents_with_invalid_live_execution': sum(r['incidents_with_invalid_live_execution'] for r in records),
            'unresolved_incidents': sum(not r['recovered'] for r in records),
            'historical_live_executions': sum(a['plan_origin'] == 'historical' for a in live_attempts),
            'generated_live_executions': sum(a['plan_origin'] == 'generated' for a in live_attempts),
            'isolated_replays': len(isolated_attempts),
            'isolated_confirmed_failures': sum(a['outcome'] == 'Confirmed failure' for a in isolated_attempts),
            'model_calls': len(calls), 'reported_usage': dict(usage), 'api_seconds': api_seconds,
            'phase_seconds': dict(phases),
        })
    comparisons = []
    for template in ('unchanged', 'preserving', 'invalidating'):
        match = {r['process']: r for r in rows if r['template'] == template}
        proposed = match['proposed']['cumulative_seconds']
        entry = {'template': template, 'proposed_cumulative_seconds': proposed}
        for comparator in ('replanning', 'full_revalidation'):
            base = match[comparator]['cumulative_seconds']
            entry[comparator] = {'comparator_cumulative_seconds': base,
                                 'proposed_minus_comparator_seconds': proposed-base,
                                 'observed_saving_seconds': base-proposed,
                                 'observed_saving_percent': (base-proposed)/base*100}
        comparisons.append(entry)
    methods = {}
    for method in ('proposed', 'replanning', 'full_revalidation'):
        subset = [r for r in rows if r['process'] == method]
        methods[method] = {key: sum(r[key] for r in subset) for key in (
            'cumulative_seconds', 'live_executions', 'successful_live_executions', 'invalid_live_executions',
            'incidents_with_invalid_live_execution', 'unresolved_incidents', 'historical_live_executions',
            'generated_live_executions', 'isolated_replays', 'isolated_confirmed_failures', 'model_calls')}
    assert sum(r['live_executions'] for r in rows) == 45
    assert sum(r['isolated_replays'] for r in rows) == 15
    assert sum(r['model_calls'] for r in rows) == 23
    assert sum(r['isolated_confirmed_failures'] for r in rows) == 4
    assert sum(r['invalid_live_executions'] for r in rows) == 0
    mean_replica_setup = sum(r['phase_seconds'].get('replica creation synchronization and fault setup', 0) for r in rows)/15
    mean_isolated_replay = sum(r['phase_seconds'].get('isolated replay and current verification', 0) for r in rows)/15
    analysis = {
        'at': time.time(), 'status': 'FINAL_DESCRIPTIVE_ANALYSIS', 'formal_incidents': 45, 'sequences': rows,
        'comparisons': comparisons, 'methods': methods,
        'mean_replica_setup_seconds': mean_replica_setup, 'mean_isolated_replay_and_verification_seconds': mean_isolated_replay,
        'accepted_pilot_incidents_excluded': 5,
        'archived_instrumentation_attempts_excluded': load(LIVE/'completion.json')['archived_instrumentation_attempts'],
        'uncertainty_policy': 'One five-incident sequence per template/process. Incidents within a sequence retain state. No independent-repeat bootstrap, significance test, non-inferiority claim or population confidence interval.',
        'registration_effort_policy': 'One-time registration and annotation labour not timed independently. Online response time excludes that effort. No break-even or monetary cost estimate.',
        'recovery_time_definition': 'Presentation through current confirmation, including 60-second fault persistence and full 210-second live verification. Includes charged isolated setup/replay and planning when used. Excludes healthy intervals and between-sequence reset.',
    }
    inputs = {name: sha for name, sha in manifest.items()}
    for name in ('protocol.json','freeze.json','completion.json','completion-audit-v1.json','retained-sequences.json','pilot-acceptance.json'):
        path = LIVE / name; inputs[str(path.relative_to(ROOT))] = digest(path)
    (OUT/'input-snapshot.json').write_text(json.dumps({'at':time.time(),'input_sha256':inputs},indent=2)+'\n')
    (OUT/'analysis.json').write_text(json.dumps(analysis,indent=2)+'\n')
    print(json.dumps({'formal_incidents':45,'comparisons':comparisons,'methods':methods,
                      'mean_replica_setup_seconds':mean_replica_setup,'mean_isolated_replay_seconds':mean_isolated_replay},indent=2))

if __name__ == '__main__':
    main()

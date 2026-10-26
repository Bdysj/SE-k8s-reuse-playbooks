"""Read-only RQ3a snapshot analysis. No imports from the live experiment runner.

The final mode requires complete240 acceptance. Preview counts only validate the
implementation; no interim inferential comparisons or paper claims are produced.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import time

METHODS = ('proposed', 'fault_matching', 'whole_environment')
DECISIONS = ('Direct reuse', 'Revalidate', 'Stop reuse')
OUTCOMES = ('Successful repair', 'Confirmed failure', 'Indeterminate', 'Not executed')
ROOT = Path(__file__).resolve().parents[2]

def sha_bytes(data): return hashlib.sha256(data).hexdigest()
def rate(n, d): return n / d if d else None

def bounds(successful, failed, uncertain):
    n = successful + failed + uncertain
    return {'execution_denominator': n, 'determinate_denominator': successful + failed,
            'outcome_evidence_coverage': rate(successful + failed, n),
            'invalid_lower': rate(failed, n), 'invalid_upper': rate(failed + uncertain, n),
            'success_lower': rate(successful, n), 'success_upper': rate(successful + uncertain, n)}

def summarize(rows):
    direct = [r for r in rows if r['initial_decision'] == 'Direct reuse' and r['executed']]
    live = [r for r in rows if r['executed']]
    outcomes = Counter(r['outcome'] for r in live)
    direct_outcomes = Counter(r['outcome'] for r in direct)
    known_direct = direct_outcomes['Successful repair'] + direct_outcomes['Confirmed failure']
    independent_successes = [r for r in rows if r.get('independent_replay_outcome') == 'Successful repair']
    return {
        'eligible_accepted_incidents': len(rows),
        'initial_decisions': {d: sum(r['initial_decision'] == d for r in rows) for d in DECISIONS},
        'initial_decision_unavailable': sum(r['initial_decision'] not in DECISIONS for r in rows),
        'no_execution': sum(not r['executed'] for r in rows),
        'physical_launch_refusals': sum(r.get('launch_blocked') is True for r in rows),
        'common_safety_blocks': sum(r.get('common_gate_blocked') is True for r in rows),
        'actual_live_executions': len(live),
        'all_live_outcomes': {o: outcomes[o] for o in OUTCOMES[:3]},
        'initial_direct_live_executions': len(direct),
        'initial_direct_outcomes': {o: direct_outcomes[o] for o in OUTCOMES[:3]},
        'determinate_initial_direct_denominator': known_direct,
        'conditional_invalid_direct_rate': rate(direct_outcomes['Confirmed failure'], known_direct),
        'conditional_successful_direct_rate': rate(direct_outcomes['Successful repair'], known_direct),
        'invalid_direct_per_eligible_incident': rate(direct_outcomes['Confirmed failure'], len(rows)),
        'direct_reuse_coverage': rate(sum(r['initial_decision'] == 'Direct reuse' for r in rows), len(rows)),
        'direct_missing_outcome_bounds': bounds(direct_outcomes['Successful repair'], direct_outcomes['Confirmed failure'], direct_outcomes['Indeterminate']),
        'all_live_missing_outcome_bounds': bounds(outcomes['Successful repair'], outcomes['Confirmed failure'], outcomes['Indeterminate']),
        'after_initial_revalidation': {
            'actual_live_executions': sum(r['executed'] and r['initial_decision'] == 'Revalidate' for r in rows),
            'outcomes': {o: sum(r['executed'] and r['initial_decision'] == 'Revalidate' and r['outcome'] == o for r in rows) for o in OUTCOMES[:3]}},
        'isolated_outcomes': {o: sum(r.get('isolated_executed') is True and r.get('isolated_outcome') == o for r in rows) for o in OUTCOMES[:3]},
        'isolated_execution_evidence_unavailable': sum(r.get('isolated_executed') is True and r.get('isolated_outcome') not in OUTCOMES[:3] for r in rows),
        'independent_successful_replay_opportunities': len(independent_successes),
        'opportunity_initial_decisions': {d: sum(r['initial_decision'] == d for r in independent_successes) for d in DECISIONS},
        'retained_initial_direct_opportunity_rate': rate(sum(r['initial_decision'] == 'Direct reuse' for r in independent_successes), len(independent_successes)),
        'note': 'Conditional reliability, incident exposure, reuse coverage and missing evidence remain distinct.'}

def matched_groups(rows):
    grouped = defaultdict(dict)
    for row in rows:
        key = (row['scenario'], row['repeat'])
        if row['method'] in grouped[key]:
            raise ValueError('Duplicate planned method in a matched block')
        grouped[key][row['method']] = row
    complete = [group for group in grouped.values() if set(group) == set(METHODS)]
    return complete, len(grouped) - len(complete)

def quantile(values, p):
    ordered = sorted(values)
    if not ordered: return None
    position = (len(ordered) - 1) * p
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)

METRICS = ('conditional_invalid_direct_rate', 'invalid_direct_per_eligible_incident',
           'direct_reuse_coverage', 'retained_initial_direct_opportunity_rate')

def paired_comparisons(rows, *, infer=False, replicates=5000, seed=20261007):
    groups, incomplete = matched_groups(rows)
    selected = [r for group in groups for r in group.values()]
    observed = {m: summarize([r for r in selected if r['method'] == m]) for m in METHODS}
    answer = {'complete_matched_blocks': len(groups), 'incomplete_matched_blocks': incomplete,
              'complete_matched_scenarios': len({r['scenario'] for r in selected}),
              'comparison_basis': 'Same scenario and registered repeat; physical responses are separately observed.',
              'comparisons': {}, 'intervals_computed': infer}
    if infer and (incomplete or len(groups) != 80 or len({r['scenario'] for r in selected}) != 40):
        raise ValueError('Final intervals require all80 matched blocks and40 scenarios')
    for other in METHODS[1:]:
        differences = {}
        for metric in METRICS:
            a, b = observed['proposed'][metric], observed[other][metric]
            differences[metric] = {'proposed': a, 'comparator': b,
                                   'difference_proposed_minus_comparator': a - b if a is not None and b is not None else None,
                                   'uncertainty': 'Not computed for an incomplete development preview'}
        answer['comparisons'][other] = differences
    if not infer:
        return answer
    if replicates < 1000:
        raise ValueError('The declared final resampling configuration requires at least1000 replicates')
    # Preserve both repeats and all three methods when sampling a scenario.
    scenarios = defaultdict(list)
    candidate_scenarios = defaultdict(list)
    for r in selected: scenarios[r['scenario']].append(r)
    for scenario, group in sorted(scenarios.items()):
        candidate_scenarios[group[0]['candidate']].append(scenario)
    if sorted(len(v) for v in candidate_scenarios.values()) != [8] * 5:
        raise ValueError('Final resampling requires the fixed five-playbook design')
    rng = random.Random(seed)
    draws = {m: {metric: [] for metric in METRICS} for m in METHODS[1:]}
    for _ in range(replicates):
        sampled = []
        for candidate in sorted(candidate_scenarios):
            choices = candidate_scenarios[candidate]
            for _ in choices: sampled.extend(scenarios[rng.choice(choices)])
        summaries = {m: summarize([r for r in sampled if r['method'] == m]) for m in METHODS}
        for other in METHODS[1:]:
            for metric in METRICS:
                a, b = summaries['proposed'][metric], summaries[other][metric]
                if a is not None and b is not None: draws[other][metric].append(a - b)
    for other in METHODS[1:]:
        for metric in METRICS:
            values = draws[other][metric]
            answer['comparisons'][other][metric]['uncertainty'] = {
                'percentile95': [quantile(values, .025), quantile(values, .975)] if values else None,
                'one_sided95_upper': quantile(values, .95), 'valid_replicates': len(values),
                'undefined_replicates': replicates - len(values),
                'degenerate': bool(values) and min(values) == max(values),
                'scope': 'Descriptive scenario resampling within five fixed playbooks, retaining repeats and paired methods.',
                'claim': 'No automatic superiority or no-increase claim. Zero events or a degenerate interval cannot establish general reliability.'}
    answer['resampling'] = {'replicates': replicates, 'seed': seed, 'unit': 'scenario within fixed playbook',
                            'playbook_population_inference': False,
                            'analysis_implementation_prepared_during_collection': True}
    return answer

class Reader:
    def __init__(self, root):
        self.root = root.resolve()
        self.identities = {}
    def read(self, path):
        path = path.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Input is outside the experiment project')
        data = path.read_bytes()
        key = str(path.relative_to(self.root))
        identity = sha_bytes(data)
        if key in self.identities and self.identities[key] != identity:
            raise ValueError('Input changed during snapshot: ' + key)
        self.identities[key] = identity
        return json.loads(data)
    def check(self, path, expected):
        path = path.resolve()
        actual = sha_bytes(path.read_bytes())
        if actual != expected:
            raise ValueError('Frozen identity mismatch: ' + str(path))
        self.identities[str(path.relative_to(self.root))] = actual
    def verify_stability(self):
        for name, identity in self.identities.items():
            if sha_bytes((self.root / name).read_bytes()) != identity:
                raise ValueError('Snapshot input changed: ' + name)

def predecision_rejection(outer, setup, *, live_record_exists, isolated_record_exists, replica_request_exists):
    if (live_record_exists or isolated_record_exists or replica_request_exists
            or outer.get('passed') is not False or outer.get('primary_post_reset_confirmed') is not True):
        raise ValueError('A pre-decision rejection requires no physical action and confirmed reset evidence')
    phase = setup.get('setup_phase')
    error = outer.get('error')
    if phase == 'fault persistence window' and error == 'Fault self-resolved or pre-existing non-target instability':
        samples = setup.get('fault_persistence', [])
        if not samples or not (samples[-1]['target']['healthy'] is True or samples[-1]['non_target']['passed'] is False):
            raise ValueError('Saved persistence gate rejection must retain its failed observation')
        injected = True
    elif phase == 'healthy observation window' and error == 'Healthy gate rejected':
        if any(key in setup for key in ('injection','fault_activation_at','fault_confirmation','fault_persistence')):
            raise ValueError('A pre-fault rejection cannot hide a fault already injected')
        samples = setup.get('healthy_window', [])
        if not samples or not (samples[-1]['target']['healthy'] is False or samples[-1]['non_target']['passed'] is False):
            raise ValueError('Saved healthy gate rejection must retain its failed observation')
        injected = False
    else:
        raise ValueError('Missing decision requires a bound registered setup gate rejection')
    return {'initial_decision': None, 'decision_stage_reached': False,
            'setup_rejection_phase': phase, 'fault_injected_before_rejection': injected}

def collect_snapshot(root, cohort, *, final=False):
    reader = Reader(root)
    protocol = reader.read(cohort / 'protocol.json')
    freeze = reader.read(cohort / 'freeze.json')
    reader.check(cohort / 'protocol.json', freeze['protocol_sha256'])
    for name, identity in freeze['source_hashes'].items():
        reader.check(root / 'tools' / name, identity)
    if not protocol['response_registered'] or len(protocol['queue']) != 240:
        raise ValueError('Registered240 response protocol required')
    for key, identity in protocol['candidate_hashes'].items():
        reader.check(root / 'candidates' / (key + '.yaml'), identity)
    reader.check(root / 'candidates/registration.v1.json', protocol['registration_sha256'])
    for item in protocol['inputs'].values():
        reader.check(root / item['path'], item['sha256'])
    reader.check(root / protocol['source_lock']['path'], protocol['source_lock']['sha256'])
    # Mutable progress is read once to select a stable completed prefix. It is
    # not added to the immutable scientific snapshot identities.
    progress_bytes = (cohort / 'progress.json').read_bytes()
    progress = json.loads(progress_bytes)
    count = progress['responses_completed']
    if not isinstance(count, int) or not 0 <= count <= 240:
        raise ValueError('Invalid completed count')
    if final:
        if count != 240 or progress['state'] != 'complete':
            raise ValueError('Final analysis cannot use an incomplete collection')
        accepted = reader.read(cohort / 'collection-acceptance.json')
        if not (accepted['accepted'] and accepted['response_trials'] == 240
                and accepted['registered_protocol_sha256'] == freeze['protocol_sha256']
                and len(accepted['record_audits']) == 240
                and all(x['accepted'] for x in accepted['record_audits'])):
            raise ValueError('Final independent collection acceptance required')
    analysis = root / 'results/formal-main-analysis-v1'
    frozen_analysis = reader.read(analysis / 'analysis-freeze.json')
    reader.check(analysis / 'trial-level-data.json', frozen_analysis['files']['trial-level-data.json'])
    parent_rows = reader.read(analysis / 'trial-level-data.json')
    independent = {(r['scenario'], r['repeat']): r for r in parent_rows if r['category'] == 'main'}
    if len(independent) != 200:
        raise ValueError('Independent RQ1 reference contains200 distinct main replays')
    rows = []
    registered_paths = set()
    for trial in protocol['queue'][:count]:
        directory = (cohort / trial['name']).resolve()
        registered_paths.add(directory)
        overridden = trial.get('acceptance_override')
        audit_path = (cohort / 'audit-overrides' / trial['name'] / 'independent-audit.json') if overridden else directory / 'independent-audit.json'
        audit = reader.read(audit_path)
        if audit.get('accepted') is not True or audit['method'] != trial['method']:
            raise ValueError('Accepted method-bound audit required')
        acceptance_path = root / overridden if overridden else directory / 'acceptance.json'
        reader.check(acceptance_path, audit['acceptance_sha256'])
        acceptance = reader.read(acceptance_path)
        for key in ('position', 'scenario', 'candidate', 'repeat', 'method', 'matched_block', 'trial_id'):
            if acceptance['trial_identity'][key] != trial[key]:
                raise ValueError('Trial identity mismatch: ' + key)
        reader.check(directory / 'primary/workflow.json', audit['workflow_sha256'])
        workflow = reader.read(directory / 'primary/workflow.json')
        for role, identity in trial['protocol_sha256'].items():
            reader.check(directory / role / 'protocol.json', identity)
        raw = directory / 'primary/live-repair/record.json'
        live = reader.read(raw) if raw.exists() else {}
        if audit['actual_live_execution'] != (live.get('action_executed') is True):
            raise ValueError('Physical execution indicator mismatch')
        if live and live['repair_outcome'] != audit['live_outcome']:
            raise ValueError('Physical outcome mismatch')
        replica_path = directory / 'replica/repair/record.json'
        replica = reader.read(replica_path) if replica_path.exists() else {}
        if (audit['actual_isolated_execution'] != (replica.get('action_executed') is True)
                or audit['isolated_execution_outcome'] != replica.get('repair_outcome')):
            raise ValueError('Isolated physical execution/outcome mismatch')
        if audit['live_outcome'] not in OUTCOMES:
            raise ValueError('Unknown repair outcome')
        if audit['actual_live_execution'] and audit['live_outcome'] == 'Confirmed failure' and not (
            live.get('execution_complete') is False or live.get('target_recovery') is False or live.get('non_target_regression') is True):
            raise ValueError('Confirmed failure has no saved failure flag')
        reference = independent[(trial['scenario'], trial['repeat'])]
        rows.append({**{k: trial[k] for k in ('position','scenario','candidate','repeat','method','matched_block','trial_id')},
                     'record_directory': str(directory.relative_to(root)),
                     'initial_decision': audit['initial_decision'], 'outcome': audit['live_outcome'],
                     'executed': audit['actual_live_execution'], 'launch_blocked': audit.get('launch_permission_blocked', False),
                     'common_gate_blocked': live.get('common_gate_blocked') is True or workflow.get('common_safety_gate', {}).get('passed') is False,
                     'isolated_executed': audit['actual_isolated_execution'],
                     'isolated_outcome': audit['isolated_execution_outcome'],
                     'independent_replay_outcome': reference['outcome'],
                     'independent_replay_trial_id': reference['trial_id'],
                     'audit_sha256': reader.identities[str(audit_path.resolve().relative_to(root))]})
    extra_rows = []
    rejected = reader.read(cohort / 'rejected-attempts.json')
    exclusions = []
    for item in rejected:
        directory = (root / item['record_directory']).resolve()
        if directory in registered_paths:
            exclusions.append({'directory': item['record_directory'], 'reason': 'Same physical attempt already accepted through a bound audit-only override; count once.'})
            continue
        trial = item['trial_identity']
        workflow_path = directory / 'primary/workflow.json'
        w = reader.read(workflow_path) if workflow_path.exists() else None
        live_path = directory / 'primary/live-repair/record.json'
        live = reader.read(live_path) if live_path.exists() else {}
        isolated_path = directory / 'replica/repair/record.json'
        isolated = reader.read(isolated_path) if isolated_path.exists() else {}
        executed = live.get('action_executed') is True
        if executed != item['candidate_action_executed'] or live.get('repair_outcome', 'Not executed') != item['live_outcome']:
            raise ValueError('Rejected physical attempt must remain bound to raw evidence')
        if w is None:
            outer = reader.read(directory / 'acceptance.json')
            setup = reader.read(directory / 'primary/incident-setup.json')
            stage = predecision_rejection(outer, setup, live_record_exists=live_path.exists(),
                isolated_record_exists=isolated_path.exists(), replica_request_exists=(directory/'replica/request.json').exists())
            if executed or item['live_outcome'] != 'Not executed':
                raise ValueError('Missing decision requires bound pre-decision rejection evidence and no physical action')
            initial_decision = None
        else:
            initial_decision = w['initial_decision']
            stage = {'decision_stage_reached': True}
        extra_rows.append({**{k:trial[k] for k in ('scenario','candidate','repeat','method','matched_block','trial_id')},
                           'initial_decision': initial_decision, 'decision_stage_reached': w is not None,
                           **stage,
                           'outcome': item['live_outcome'], 'executed': executed,
                           'launch_blocked': live.get('launch_permission_blocked') is True,
                           'common_gate_blocked': live.get('common_gate_blocked') is True,
                           'isolated_executed': isolated.get('action_executed') is True,
                           'isolated_outcome': isolated.get('repair_outcome'),
                           'record_directory': item['record_directory'], 'extra_unaccepted_attempt': True,
                           'rejection_reasons': item['audit']['failures']})
    reader.verify_stability()
    return {'rows': rows, 'extra_rows': extra_rows, 'rejected_entries': len(rejected),
            'deduplicated_rejections': exclusions, 'input_sha256': reader.identities,
            'progress_read_at': time.time(), 'progress_snapshot_sha256': sha_bytes(progress_bytes),
            'completed_at_snapshot': count, 'planned_responses': 240,
            'formal_complete': final, 'cohort': str(cohort.relative_to(root))}

def build(snapshot, *, final=False, replicates=5000):
    rows, extras = snapshot['rows'], snapshot['extra_rows']
    per_method = {m: summarize([r for r in rows if r['method'] == m]) for m in METHODS}
    per_playbook = {candidate: {m: summarize([r for r in rows if r['candidate'] == candidate and r['method'] == m])
                                for m in METHODS} for candidate in sorted({r['candidate'] for r in rows})}
    all_attempts = {}
    for method in METHODS:
        attempts = [r for r in rows + extras if r['method'] == method]
        policy_attempts = [r for r in attempts if r['initial_decision'] in DECISIONS]
        if any(r['executed'] for r in attempts if r['initial_decision'] not in DECISIONS):
            raise ValueError('An actual execution cannot be excluded as a pre-decision setup failure')
        summary = summarize(policy_attempts)
        summary['policy_stage_attempts_including_unaccepted'] = summary.pop('eligible_accepted_incidents')
        summary.update(included_collection_attempts=len(attempts),
                       predecision_setup_rejections=len(attempts)-len(policy_attempts))
        all_attempts[method] = summary
    comparisons = paired_comparisons(rows, infer=final, replicates=replicates)
    return {'status': 'FINAL_DESCRIPTIVE_ANALYSIS' if final else 'DEVELOPMENT_PREVIEW_NOT_FINAL_RESULTS',
            'completed_at_snapshot': snapshot['completed_at_snapshot'], 'planned_responses': 240,
            'per_method': per_method, 'per_playbook': per_playbook, 'matched_comparisons': comparisons,
            'all_unique_physical_attempts_sensitivity': all_attempts,
            'extra_unaccepted_attempts': extras, 'rejected_entries': snapshot['rejected_entries'],
            'deduplicated_rejections': snapshot['deduplicated_rejections'],
            'all_attempts_scope': 'Operational sensitivity only; extra attempts are not added to planned paired blocks. Policy-stage rates include rejected attempts that reached a decision. Pre-decision setup rejections are counted separately and supply no invented decision or repair failure.',
            'initial_reuse_opportunities': 'Joined to independently collected RQ1 scenario/repeat outcomes, not current response success or evidence-state expert labels.',
            'unavailable_measures': ['RQ3b incident-to-recovery comparisons', 'independent expert agreement'],
            'interpretation': 'Report denominators and evidence coverage with reliability. An incomplete prefix is not a balanced final experiment.'}

def current_cohort(root):
    central = json.loads((root/'results/RQ3a-response-preparation-v2/progress.json').read_text())
    name = central['formal_progress']
    path = (root/name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.parent.name.startswith('formal-rq3a-responses-v') or path.name != 'progress.json':
        raise ValueError('Active registered response cohort reference required')
    return path.parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cohort', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--final', action='store_true')
    args = parser.parse_args()
    cohort = (args.cohort or current_cohort(ROOT)).resolve(); output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'results/offline-parallel-v1'):
        raise ValueError('Write only to the new offline-work output directory')
    if output.exists():
        raise ValueError('Choose a new output directory; never overwrite an earlier snapshot')
    snapshot = collect_snapshot(ROOT, cohort, final=args.final)
    result = build(snapshot, final=args.final)
    output.mkdir(parents=True)
    for name, value in [('input-snapshot.json', snapshot), ('analysis.json', result)]:
        (output / name).write_text(json.dumps(value, indent=2) + '\n')
    from figures import create_figures
    create_figures(result, output)
    files = {p.name: sha_bytes(p.read_bytes()) for p in output.iterdir() if p.is_file()}
    (output / 'output-identities.json').write_text(json.dumps(files, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'status': result['status'],
                      'completed_snapshot': snapshot['completed_at_snapshot'],
                      'paired_blocks': result['matched_comparisons']['complete_matched_blocks'],
                      'extra_attempts_preserved': len(snapshot['extra_rows'])}))

if __name__ == '__main__':
    main()

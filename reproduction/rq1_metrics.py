"""Pure helpers copied from tools/analyse_formal_main_v1.py.
Source SHA256: 50a3dd9626ee0d4b987297b694368b21d589ecc88018e51ca11042ecc78fd7a3
"""
import collections
import random
METHODS = ('proposed', 'fault_matching', 'whole_environment')

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

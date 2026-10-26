"""Adversarial admission tests; synthetic implementation checks, not repair data."""
import copy
import unittest
from unittest.mock import patch

import reuse_assessor as a
import rq3a_response_workflow as w

CONFIG = {'observation_ttl': 30, 'refresh_budget': 1, 'replay_budget': 1,
          'total_budget_seconds': 600, 'max_assessments': 6}


class BaselineBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.current = {'criterion': 'current'}
        self.observed = {kind: [] for kind in w.methods.SCOPE}
        self.observed.update(started_at=99, completed_at=100, relevant_revision='A')
        self.criteria = {'current': True}
        self.reference = {'snapshot': {k: [] for k in w.methods.SCOPE}, 'criteria': {'old': True}}
        self.cert = {'type': 'immutable_actual_isolated_outcome_v1', 'candidate_sha256': 'fixed',
            'contract_sha256': a.digest(self.current), 'relevant_revision': 'A',
            'completed_at': 99, 'outcome': 'Successful repair', 'fidelity_passed': True,
            'verified_requirements': list(w.CONDITIONS)}
        self.incident = {'workload': ['web'], 'symptoms': [], 'error_codes': []}
        self.model = w.policies.fit_fault_matching({'P5': [self.incident]}, [
            {'candidate': 'P5', 'incident': self.incident, 'outcome': 'Successful repair',
             'label_source': 'observed_development_replay'},
            {'candidate': 'P5', 'incident': {'workload': ['other']}, 'outcome': 'Confirmed failure',
             'label_source': 'observed_development_replay'}])

    def run_whole(self, **changes):
        args = dict(config=CONFIG, reference=self.reference, criteria=self.criteria,
            validate=lambda *_: copy.deepcopy(self.cert), verify_validation=lambda _: True,
            clock=lambda: 100, monotonic=lambda: 100)
        args.update(changes)
        return w.evaluate('whole_environment', 'P5', 'fixed', self.current,
            args.pop('collect', lambda *_: copy.deepcopy(self.observed)),
            args.pop('gate', lambda *_: {'passed': True}), **args)

    def test_fault_matching_uses_only_model_and_common_gate(self):
        with patch.object(a, 'compare', side_effect=AssertionError('Proposed logic called')):
            r = w.evaluate('fault_matching', 'P5', 'fixed', self.current,
                lambda *_: self.observed, lambda *_: {'passed': True}, config=CONFIG,
                incident=self.incident, model=self.model, clock=lambda: 100, monotonic=lambda: 100)
        self.assertTrue(r['execution_permitted'])
        self.assertEqual(r['replays'], 0)
        self.assertFalse(r['condition_states_used_for_baseline_admission'])
        r = w.evaluate('fault_matching', 'P5', 'fixed', self.current, lambda *_: self.observed,
            lambda *_: {'passed': False}, config=CONFIG, incident=self.incident, model=self.model,
            clock=lambda: 100, monotonic=lambda: 100)
        self.assertEqual(r['initial_decision'], 'Direct reuse')
        self.assertFalse(r['execution_permitted'])

    def test_fault_nonmatch_never_requests_replica(self):
        def prohibited(*_):
            self.fail('Nonmatching fault baseline requested replay')
        r = w.evaluate('fault_matching', 'P5', 'fixed', self.current, lambda *_: self.observed,
            lambda *_: {'passed': True}, config=CONFIG, incident={'workload': ['other']},
            model=self.model, validate=prohibited, clock=lambda: 100, monotonic=lambda: 100)
        self.assertEqual(r['initial_decision'], 'Stop reuse')
        self.assertFalse(r['execution_permitted'])

    def test_whole_environment_pass_is_independent_of_proposed_contradiction(self):
        with patch.object(a, 'compare', side_effect=AssertionError('Proposed logic called')):
            r = self.run_whole()
        self.assertEqual(r['initial_decision'], 'Revalidate')
        self.assertTrue(r['execution_permitted'])
        self.assertEqual(r['replays'], 1)
        self.assertTrue(w.permission_boundary(r, self.observed, 'fixed', self.current,
                                             now=100, verify_validation=lambda _: True)['passed'])

    def test_whole_equal_can_direct_without_replica(self):
        reference = copy.deepcopy(self.reference); reference['criteria'] = self.criteria
        r = self.run_whole(reference=reference, validate=None, verify_validation=None)
        self.assertEqual(r['initial_decision'], 'Direct reuse')
        self.assertTrue(r['execution_permitted'])
        self.assertEqual(r['replays'], 0)

    def test_failed_incomplete_expired_future_wrong_candidate_contract_revision_cannot_allow(self):
        mutations = [dict(outcome='Confirmed failure'), dict(outcome='Indeterminate'),
            dict(completed_at=1), dict(completed_at=101), dict(candidate_sha256='other'),
            dict(contract_sha256='other'), dict(relevant_revision='other'),
            dict(fidelity_passed=False), dict(verified_requirements=['target_objects'])]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                cert = {**self.cert, **mutation}
                r = self.run_whole(validate=lambda *_, c=cert: c)
                self.assertFalse(r['execution_permitted'])

    def test_provenance_audit_is_required_and_failures_are_preserved(self):
        for verify in (None, lambda _: False):
            self.assertFalse(self.run_whole(verify_validation=verify)['execution_permitted'])
        def unavailable(_):
            raise RuntimeError('Immutable record changed')
        r = self.run_whole(verify_validation=unavailable)
        self.assertFalse(r['execution_permitted'])
        self.assertTrue(any('Immutable record changed' in x.get('error', '') for x in r['events']))

    def test_changed_dependency_or_unrelated_broad_configuration_blocks_certificate(self):
        for kind in ('dependency', 'broad'):
            changed = copy.deepcopy(self.observed)
            if kind == 'dependency':
                changed['relevant_revision'] = 'B'
            else:
                changed['configmaps'] = [{'metadata': {'name': 'unrelated'}, 'data': {'x': 'changed'}}]
            observations = iter([self.observed, changed])
            r = self.run_whole(collect=lambda *_: next(observations))
            self.assertFalse(r['execution_permitted'])

    def test_gate_time_cannot_renew_old_outcome(self):
        tick = [100]
        def gate(*_):
            tick[0] = 130
            return {'passed': True}
        r = self.run_whole(gate=gate, clock=lambda: tick[0])
        self.assertFalse(r['execution_permitted'])
        self.assertEqual(r['validation_certificate']['completed_at'], 99)

    def test_overall_validation_budget_is_real(self):
        tick = [100]
        def validate(*_):
            tick[0] = 701
            return self.cert
        self.assertFalse(self.run_whole(validate=validate, monotonic=lambda: tick[0])['execution_permitted'])

    def test_live_launch_rechecks_all_permission_bindings(self):
        r = self.run_whole()
        changed = copy.deepcopy(self.observed); changed['relevant_revision'] = 'B'
        checks = [(self.observed, 'other', self.current, 100),
                  (self.observed, 'fixed', {'criterion': 'other'}, 100),
                  (changed, 'fixed', self.current, 100),
                  (self.observed, 'fixed', self.current, 130)]
        for observed, digest, current, now in checks:
            self.assertFalse(w.permission_boundary(r, observed, digest, current, now=now,
                                                  verify_validation=lambda _: True)['passed'])
        self.assertFalse(w.permission_boundary(r, self.observed, 'fixed', self.current,
                                              now=100, verify_validation=lambda _: False)['passed'])

    def test_no_replay_budget_and_collector_failure_decline(self):
        config = {**CONFIG, 'replay_budget': 0}
        self.assertFalse(self.run_whole(config=config)['execution_permitted'])
        def unavailable(*_):
            raise RuntimeError('API unavailable')
        r = self.run_whole(collect=unavailable)
        self.assertFalse(r['execution_permitted'])
        self.assertEqual(r['refreshes'], 1)
        self.assertEqual(r['replays'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)

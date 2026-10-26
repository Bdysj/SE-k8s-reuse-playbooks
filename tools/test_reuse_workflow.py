"""Synthetic workflow acceptance checks; no experiment outcome labels."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import reuse_assessor as a
import reuse_workflow as w
from test_reuse_assessor import inputs

CONFIG = {'observation_ttl': 30, 'refresh_budget': 1, 'replay_budget': 1,
          'total_budget_seconds': 300, 'max_assessments': 6}


class WorkflowTests(unittest.TestCase):
    def run_workflow(self, snapshots=None, current=None, validate=None, gate=None, config=None):
        snapshot, default = inputs()
        snapshots = iter(snapshots or [snapshot]*10)
        history = {'registered': {'requirements': ['immutable']}}
        original = copy.deepcopy(history)
        result = w.evaluate('P1', 'fixed', current or default, history,
            lambda *_: next(snapshots), gate or (lambda *_: {'passed': True}),
            config=config or CONFIG, validate=validate, clock=lambda: 100,
            monotonic=lambda: 100)
        self.assertEqual(history, original)
        self.assertTrue(result['history_preserved'])
        return result

    def test_direct_has_fresh_execution_permission(self):
        result = self.run_workflow()
        self.assertEqual(result['initial_decision'], 'Direct reuse')
        self.assertTrue(result['execution_permitted'])

    def test_common_safety_gate_does_not_turn_into_policy_success(self):
        result = self.run_workflow(gate=lambda *_: {'passed': False, 'failures': ['scope']})
        self.assertEqual(result['initial_decision'], 'Direct reuse')
        self.assertFalse(result['execution_permitted'])
        self.assertTrue(result['fallback_required'])

    def test_unknown_unavailable_replay_is_never_success(self):
        _, current = inputs(); current['cart_check'] = True
        result = self.run_workflow(current=current)
        self.assertEqual(result['initial_decision'], 'Revalidate')
        self.assertEqual(result['final_decision'], 'Stop reuse')
        self.assertFalse(result['execution_permitted'])

    def test_fresh_matching_replay_preserves_initial_decision(self):
        _, current = inputs(); current['cart_check'] = True
        def validate(candidate, h, snapshot, contract, unresolved, budget):
            return {'candidate_sha256': h, 'contract_sha256': a.digest(contract),
                'fidelity_passed': True, 'outcome': 'Successful repair',
                'completed_at': 99, 'relevant_revision': snapshot['relevant_revision']}
        result = self.run_workflow(current=current, validate=validate)
        self.assertEqual(result['initial_decision'], 'Revalidate')
        self.assertEqual(result['final_decision'], 'Direct reuse')
        self.assertEqual(result['replays'], 1)
        self.assertEqual(result['execution_route'], 'Replay after evidence revalidation')

    def test_incident_change_invalidates_replica_result(self):
        snapshot, current = inputs(); current['cart_check'] = True
        changed = copy.deepcopy(snapshot); changed['relevant_revision'] = 'revision-B'
        def validate(*args):
            return {'candidate_sha256': 'fixed', 'contract_sha256': a.digest(current),
                'fidelity_passed': True, 'outcome': 'Successful repair',
                'completed_at': 99, 'relevant_revision': 'revision-A'}
        result = self.run_workflow([snapshot, snapshot, changed], current, validate)
        self.assertEqual(result['final_decision'], 'Stop reuse')
        self.assertFalse(result['execution_permitted'])

    def test_continual_changes_are_bounded(self):
        snapshot, _ = inputs()
        observations = []
        for i in range(20):
            value = copy.deepcopy(snapshot); value['relevant_revision'] = str(i)
            observations.append(value)
        result = self.run_workflow(observations)
        self.assertEqual(len(result['assessments']), CONFIG['max_assessments'])
        self.assertFalse(result['execution_permitted'])

    def test_incomplete_validation_exhausts_budget(self):
        _, current = inputs(); current['cart_check'] = True
        result = self.run_workflow(current=current, validate=lambda *_: {'outcome': 'Indeterminate'})
        self.assertEqual(result['replays'], 1)
        self.assertEqual(result['final_decision'], 'Stop reuse')

    def test_observation_only_gap_can_resolve_without_replay(self):
        snapshot, _ = inputs(); stale = copy.deepcopy(snapshot); stale['started_at'] = 1
        result = self.run_workflow([stale, snapshot, snapshot])
        self.assertEqual(result['initial_decision'], 'Revalidate')
        self.assertEqual(result['refreshes'], 1)
        self.assertEqual(result['replays'], 0)
        self.assertTrue(result['execution_permitted'])

    def test_omission_applies_to_revalidation_budget(self):
        _, current = inputs(); current['cart_check'] = True
        snapshot, _ = inputs()
        result = w.evaluate('P1', 'fixed', current, {}, lambda *_: snapshot,
            lambda *_: {'passed': True}, config=CONFIG,
            omitted='verification_criteria', clock=lambda: 100, monotonic=lambda: 100)
        self.assertEqual(result['initial_decision'], 'Direct reuse')
        self.assertEqual(result['replays'], 0)

    def test_budget_validation(self):
        for key, value in [('replay_budget', 1.5), ('total_budget_seconds', float('inf')),
                           ('refresh_budget', True), ('max_assessments', 0)]:
            config = {**CONFIG, key: value}
            with self.assertRaises(ValueError):
                self.run_workflow(config=config)

    def test_existing_history_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'assessment.json'
            w.save_new(path, {'first': True})
            with self.assertRaises(FileExistsError):
                w.save_new(path, {'second': True})
            self.assertEqual(json.loads(path.read_text()), {'first': True})


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Regressions for a declined immutable-selector incident and failed cleanup."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rq3a_incident_reset as cleanup


class ResetBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.row = {'id': 'P5-2-2', 'construction':
            {'target_deployment': 'web', 'delete_deployments_before': ['web']}}
        self.protocol = {'context': 'k3d-lab', 'namespace': 'robot-shop',
                         'manifest': 'baseline.yaml', 'scenarios': [self.row]}

    def run_reset(self, kubectl, reset):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(cleanup.rb, 'CONTEXT', 'k3d-lab'), \
             patch.object(cleanup.rb, 'NS', 'robot-shop'), \
             patch.object(cleanup.rb, 'declared_fingerprint', return_value={
                 'workloads': {'web': {'kind': 'Deployment'}, 'mysql': {'kind': 'Deployment'}}}), \
             patch.object(cleanup.rb, 'kubectl', side_effect=kubectl), \
             patch.object(cleanup.reg, 'reset_s4', side_effect=reset), \
             patch.object(cleanup.ex, 'sha', return_value='fixture-digest'):
            cleanup.reset_incident(self.row, self.protocol, Path(directory)/'reset.log')

    def test_declined_immutable_selector_is_recreated_before_reset(self):
        existing = {'web'}
        def delete(*args):
            if args[1] == 'deployment':
                self.assertEqual(args[2], 'web'); existing.remove('web')
        def reset(*args):
            if 'web' in existing:
                raise RuntimeError('Deployment selector is immutable')
        self.run_reset(delete, reset)

    def test_failed_deletion_cannot_be_reported_as_confirmed_reset(self):
        calls = []
        def delete(*args):
            if args[1] == 'deployment':
                raise RuntimeError('API deletion rejected')
        with self.assertRaisesRegex(RuntimeError, 'API deletion rejected'):
            self.run_reset(delete, lambda *args: calls.append('reset'))
        self.assertEqual(calls, [])

    def test_unrelated_database_cannot_be_deleted_by_target_cleanup(self):
        self.row['construction']['delete_deployments_before'] = ['mysql']
        calls = []
        with self.assertRaisesRegex(RuntimeError, 'beyond its declared target'):
            self.run_reset(lambda *args: calls.append(args), lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_context_mismatch_fails_before_any_mutation(self):
        self.protocol['context'] = 'k3d-lab-validation'
        calls = []
        with self.assertRaisesRegex(RuntimeError, 'context/namespace'):
            self.run_reset(lambda *args: calls.append(args), lambda *args: calls.append(args))
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()

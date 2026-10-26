"""Accept renamed logical targets while preserving post-decision deletion bounds."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import rq3a_incident_reset_v2 as cleanup


class LogicalResetBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.row = {'id': 'P1-1-2', 'construction': {'target_deployment': 'storefront',
            'target_container': 'web', 'delete_deployments_before': ['web']}}
        self.protocol = {'context': 'k3d-lab', 'namespace': 'robot-shop',
                         'manifest': 'baseline.yaml', 'scenarios': [self.row]}
        self.workloads = {'web': {'kind': 'Deployment', 'containers': {'web': {}}},
            'ratings': {'kind': 'Deployment', 'containers': {'ratings': {}}},
            'mysql': {'kind': 'Deployment', 'containers': {'mysql': {}}}}

    def test_renamed_web_can_restore_registered_original_identity(self):
        self.assertEqual(cleanup.validate_scope(self.row, self.protocol, self.workloads), ['web'])

    def test_renamed_feedback_can_restore_ratings(self):
        self.row['construction'].update(target_deployment='feedback', target_container='ratings',
                                        delete_deployments_before=['ratings'])
        self.assertEqual(cleanup.validate_scope(self.row, self.protocol, self.workloads), ['ratings'])

    def test_unrelated_database_still_rejected(self):
        self.row['construction']['delete_deployments_before'] = ['mysql']
        with self.assertRaisesRegex(RuntimeError, 'logical target'):
            cleanup.validate_scope(self.row, self.protocol, self.workloads)

    def test_unfrozen_construction_rejected(self):
        other = copy.deepcopy(self.row); other['construction']['target_deployment'] = 'changed'
        with self.assertRaisesRegex(RuntimeError, 'exact frozen'):
            cleanup.validate_scope(other, self.protocol, self.workloads)

    def exercise_reset(self, delete, reset):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(cleanup.rb, 'CONTEXT', 'k3d-lab'), \
             patch.object(cleanup.rb, 'NS', 'robot-shop'), \
             patch.object(cleanup.rb, 'declared_fingerprint', return_value={'workloads': self.workloads}), \
             patch.object(cleanup.rb, 'kubectl', side_effect=delete), \
             patch.object(cleanup.reg, 'reset_s4', side_effect=reset), \
             patch.object(cleanup.ex, 'sha', return_value='fixture-digest'):
            cleanup.reset_incident(self.row, self.protocol, Path(directory)/'reset.log')

    def test_cleanup_precedes_reset_for_actual_declared_name(self):
        calls = []
        self.exercise_reset(lambda *args: calls.append(args), lambda *args: calls.append(('reset',)))
        self.assertEqual(calls[1][0:3], ('delete', 'deployment', 'web'))
        self.assertEqual(calls[-1], ('reset',))

    def test_delete_failure_stops_reset(self):
        calls = []
        def delete(*args):
            if args[1] == 'deployment':
                raise RuntimeError('delete failed')
        with self.assertRaisesRegex(RuntimeError, 'delete failed'):
            self.exercise_reset(delete, lambda *args: calls.append('reset'))
        self.assertEqual(calls, [])

    def test_context_mismatch_cannot_mutate(self):
        self.protocol['context'] = 'k3d-lab-validation'; calls = []
        with self.assertRaisesRegex(RuntimeError, 'context/namespace'):
            self.exercise_reset(lambda *args: calls.append(args), lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_all_forty_registered_cleanup_scopes(self):
        root = cleanup.ex.ROOT
        approved = json.loads((root/'results/RQ3a-response-preparation-v2/plan.json').read_text())
        original = json.loads((root/approved['predecessor']).read_text())
        with patch.object(cleanup.rb, 'MANIFEST', root/original['manifest']):
            declared = cleanup.rb.declared_fingerprint()['workloads']
        self.assertEqual(len(approved['scenario_definitions']), 40)
        for row in approved['scenario_definitions']:
            with self.subTest(scenario=row['id']):
                self.assertEqual(cleanup.validate_scope(row, {'scenarios': [row]}, declared),
                                 row['construction'].get('delete_deployments_before', []))


if __name__ == '__main__':
    unittest.main(verbosity=2)

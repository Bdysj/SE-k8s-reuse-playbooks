"""Pre-launch refusals must continue safely; incomplete executions must still stop."""
import copy
import json
import unittest
from unittest.mock import patch
from pathlib import Path

import rq3a_launch_audit_v3 as audit


class RefusalBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = audit.ex.ROOT/'results/formal-rq3a-responses-v2/039-P3-4-1-r1-whole_environment/primary/live-repair/record.json'
        cls.record = json.loads(cls.path.read_text())
        cls.timing = json.loads((cls.path.parent.parent/'protocol.json').read_text())['timing']

    def check(self, record):
        return audit.refusal_checks(record, self.timing)

    def test_actual39_is_proven_not_executed_not_a_repair_failure(self):
        result = audit.audit(self.path)
        self.assertTrue(result['accepted'], result['failures'])
        self.assertTrue(result['blocked_before_action'])
        self.assertEqual(result['launch_refusal_category'], 'stale_launch_observation')
        self.assertEqual(result['repair_outcome'], 'Not executed')

    def test_refusal_classes_have_explicit_independent_proofs(self):
        cases = [
            ('Decision execution boundary blocked candidate', 'decision_boundary_refusal'),
            ('Common safety gate blocked the candidate: scope', 'common_gate_refusal'),
            ('Pre-action policy observation became stale before launch', 'stale_launch_observation'),
            ('Synchronous pre-action gate became stale before launch', 'stale_synchronous_check'),
            ('Validation or permission expired at actual action launch', 'expired_launch_permission'),
            ('Pre-action evidence exceeded its fixed age budget', 'stale_non_target_check'),
            ('Pre-action instability: no candidate launched', 'non_target_prelaunch_refusal')]
        for reason, category in cases:
            with self.subTest(category=category):
                r = copy.deepcopy(self.record); r['collection_or_setup_error'] = reason
                r['outcome_fixed_at'] = 200
                r['completed_at'] = 210
                r['decision_execution_boundary'].update(passed=False, permission_valid_until=150)
                r['common_gate_blocked'] = True; r['common_safety_gate']['passed'] = False
                r['pre_action_policy_observation']['started_at'] = 100
                r['non_target_immediately_before_action'].update(started_at=100, completed_at=150, passed=False)
                r['pre_action_instability'] = True
                result = self.check(r)
                self.assertTrue(result['accepted'], result['failures'])
                self.assertEqual(result['launch_refusal_category'], category)

    def test_error_text_without_expiry_proof_is_rejected(self):
        r = copy.deepcopy(self.record)
        r['pre_action_policy_observation']['started_at'] = r['outcome_fixed_at']-1
        self.assertFalse(self.check(r)['accepted'])

    def test_unknown_api_exception_is_not_normalised_to_refusal(self):
        r = copy.deepcopy(self.record); r['collection_or_setup_error'] = 'kubectl TLS timeout'
        self.assertFalse(self.check(r)['accepted'])

    def test_action_started_cannot_be_hidden_as_no_execution(self):
        r = copy.deepcopy(self.record); r['action_started_at'] = r['started_at']
        self.assertFalse(self.check(r)['accepted'])

    def test_executed_action_cannot_be_hidden_as_refusal(self):
        r = copy.deepcopy(self.record); r['action_executed'] = True
        self.assertFalse(self.check(r)['accepted'])

    def test_unconfirmed_reset_still_stops(self):
        r = copy.deepcopy(self.record); r['post_reset_confirmed'] = False
        self.assertFalse(self.check(r)['accepted'])

    def test_missing_proof_and_false_success_still_stop(self):
        r = copy.deepcopy(self.record); r['repair_outcome'] = 'Successful repair'
        self.assertFalse(self.check(r)['accepted'])
        r = copy.deepcopy(self.record); del r['outcome_fixed_at']
        self.assertFalse(self.check(r)['accepted'])

    def test_incomplete_executed_trace_returns_rejection_not_safe_refusal(self):
        r = copy.deepcopy(self.record); r['action_executed'] = True
        read_text = Path.read_text
        def altered(path, *args, **kwargs):
            return json.dumps(r) if path == self.path else read_text(path, *args, **kwargs)
        with patch.object(Path, 'read_text', altered):
            answer = audit.audit(self.path)
        self.assertFalse(answer['accepted'])
        self.assertFalse(answer['blocked_before_action'])

    def test_saved38_existing_routes_remain_independently_accepted(self):
        from audit_rq3a_response_pilots_v3 import audit_case
        root = audit.ex.ROOT/'results/formal-rq3a-responses-v2'
        protocol = json.loads((root/'protocol.json').read_text())
        for trial in protocol['queue'][:38]:
            with self.subTest(position=trial['position'], method=trial['method']):
                result = audit_case(root/trial['name'])
                self.assertTrue(result['accepted'], result['failures'])
                self.assertEqual(result['acceptance_sha256'],
                    json.loads((root/trial['name']/'independent-audit.json').read_text())['acceptance_sha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

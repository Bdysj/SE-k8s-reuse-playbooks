"""Regression checks for missing historical names and unchanged experiment logic."""
import ast
import copy
import pathlib
import unittest
from unittest.mock import patch

import historical_business_v3 as historical
import qualification as q
import reset_baseline as rb


class HistoricalObserverChecks(unittest.TestCase):
    def test_absent_old_name_keeps_http_evidence_and_is_not_healthy(self):
        http = {'healthy': True, 'status': 200, 'curl_rc': 0, 'content_match': True}
        with patch.object(q, 'request', return_value=copy.deepcopy(http)), \
                patch.object(rb, 'kubectl', return_value='') as get, \
                patch.object(q, 'current_rollout') as rollout:
            result = historical.observe('P3')
        self.assertEqual(result['status'], 200)
        self.assertEqual(result['curl_rc'], 0)
        self.assertFalse(result['healthy'])
        self.assertTrue(result['historical_subject_absent'])
        self.assertFalse(result['rollout']['complete'])
        self.assertIn('--ignore-not-found', get.call_args.args)
        self.assertNotIn('allow_fail', get.call_args.kwargs)
        rollout.assert_not_called()

    def test_api_timeout_and_permission_failures_are_not_absence(self):
        for error in ['context deadline exceeded', 'Forbidden']:
            with patch.object(q, 'request', return_value={'healthy': True}), \
                    patch.object(rb, 'kubectl', side_effect=rb.Failure(error)):
                with self.assertRaises(rb.Failure):
                    historical.observe('P3')

    def test_present_subject_retains_exact_original_predicate(self):
        for http_healthy, complete in [(True, True), (True, False), (False, True)]:
            with patch.object(q, 'request', return_value={'healthy': http_healthy, 'status': 200}), \
                    patch.object(rb, 'kubectl', return_value='{"kind":"Deployment"}'), \
                    patch.object(q, 'current_rollout', return_value={'complete': complete}):
                result = historical.observe('P3')
            self.assertEqual(result['healthy'], http_healthy and complete)
            self.assertNotIn('historical_subject_absent', result)

    def test_probe_failures_still_stop_collection(self):
        with patch.object(q, 'request', side_effect=RuntimeError('Evidence collection failed')):
            with self.assertRaises(RuntimeError):
                historical.observe('P3')

    def test_other_families_do_not_add_a_rollout_requirement(self):
        for key in ['P1', 'P2', 'P4', 'P5']:
            value = {'healthy': True, 'status': 200}
            with patch.object(q, 'request', return_value=value), patch.object(rb, 'kubectl') as get:
                self.assertEqual(historical.observe(key), value)
                get.assert_not_called()

    def test_executor_diff_only_changes_diagnostic_call_and_provenance(self):
        root = pathlib.Path(__file__).resolve().parent
        old = (root / 'formal_replay_executor_v2.py').read_text()
        expected = old.replace('import qualification as q\n',
                               'import qualification as q\nimport historical_business_v3 as historical\n')
        expected = expected.replace("'protocol_sha256':p['protocol_sha256'], 'samples':[],",
                                    "'protocol_sha256':p['protocol_sha256'], "
                                    "'collector_amendment_sha256':p['collector_amendment_sha256'], 'samples':[],")
        expected = expected.replace("t['historical_business_check']=q.observe(key)",
                                    "t['historical_business_check']=historical.observe(key)")
        actual = (root / 'formal_replay_executor_v3.py').read_text()
        self.assertEqual(ast.dump(ast.parse(actual)), ast.dump(ast.parse(expected)))


if __name__ == '__main__':
    unittest.main()

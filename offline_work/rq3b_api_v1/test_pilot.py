"""Check that API failures stop before planning and old evidence survives reruns."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import pilot


class PilotChecks(unittest.TestCase):
    def message(self, value, *, status='completed', tokens=20):
        return {'model': 'gpt-6-sol', 'status': status, 'usage': {'output_tokens': tokens},
                'output': [{'type': 'message', 'role': 'assistant',
                            'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}

    def run_pilot(self, *, cap_pass=True, available=True, malformed_plan=False, smoke_value=True):
        calls = []
        def transport(directory, payload, **kwargs):
            directory.mkdir()
            calls.append((directory.name, payload))
            if directory.name == '01-models':
                result = {'data': [{'id': 'gpt-6-sol' if available else 'another-model'}]}
            elif directory.name == '02-structured-smoke':
                result = self.message({'ok': smoke_value})
            elif directory.name == '03-responses-boundary':
                result = self.message(None, status='incomplete' if cap_pass else 'completed', tokens=16 if cap_pass else 1005)
                result['incomplete_details'] = {'reason': 'max_output_tokens'} if cap_pass else None
            elif directory.name == '04-chat-boundary':
                result = {'model': 'gpt-6-sol', 'usage': {'completion_tokens': 16},
                          'choices': [{'finish_reason': 'length'}]}
            else:
                result = self.message([] if malformed_plan else {
                    'status': 'candidate', 'plan_yaml': '- hosts: localhost', 'rationale': 'Current observations'})
            return result, {'http_completed': True}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'new-check'
            with patch.object(pilot, 'request', side_effect=transport), contextlib.redirect_stdout(io.StringIO()):
                result = pilot.run(output)
            saved = json.loads((output / 'acceptance.json').read_text())
        return result, saved, calls

    def test_ignored_responses_cap_stops_planning_even_if_chat_passes(self):
        result, saved, calls = self.run_pilot(cap_pass=False)
        self.assertFalse(result['transport_budget_checks_passed'])
        self.assertTrue(result['chat_token_boundary_passed'])
        self.assertFalse(result['saved_observation_planning_passed'])
        self.assertEqual([name for name, _ in calls],
                         ['01-models', '02-structured-smoke', '03-responses-boundary', '04-chat-boundary'])
        self.assertEqual(result, saved)

    def test_unavailable_model_stops_after_discovery_and_archives_reason(self):
        result, saved, calls = self.run_pilot(available=False)
        self.assertEqual(len(calls), 1)
        self.assertIn('unavailable', saved['stop_reason'])
        self.assertFalse(result['saved_observation_planning_passed'])

    def test_numeric_one_is_not_a_boolean_smoke_result(self):
        result, saved, calls = self.run_pilot(smoke_value=1)
        self.assertFalse(result['structured_smoke_passed'])
        self.assertFalse(result['transport_budget_checks_passed'])
        self.assertFalse(result['saved_observation_planning_passed'])
        self.assertEqual(len(calls), 4)

    def test_valid_controls_do_not_enable_formal_or_live_execution(self):
        result, saved, calls = self.run_pilot()
        self.assertTrue(result['transport_budget_checks_passed'])
        self.assertTrue(result['saved_observation_planning_passed'])
        self.assertFalse(result['formal_protocol_accepted'])
        self.assertEqual(result['formal_trials'], 0)
        self.assertEqual(result['live_recovery_trials'], 0)
        self.assertEqual(calls[-1][1]['max_output_tokens'], 2048)
        self.assertEqual(calls[-1][1]['tools'], [])

    def test_malformed_plan_is_rejected_after_good_controls(self):
        result, saved, calls = self.run_pilot(malformed_plan=True)
        self.assertTrue(result['transport_budget_checks_passed'])
        self.assertFalse(saved['saved_observation_planning_passed'])

    def test_existing_evidence_is_not_overwritten_or_queried(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'old'; output.mkdir()
            evidence = output / 'acceptance.json'; evidence.write_text('original evidence')
            with patch.object(pilot, 'request') as transport:
                with self.assertRaises(FileExistsError):
                    pilot.run(output)
                transport.assert_not_called()
            self.assertEqual(evidence.read_text(), 'original evidence')


if __name__ == '__main__':
    unittest.main()

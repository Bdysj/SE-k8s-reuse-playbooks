import json
import unittest
from pathlib import Path
from .client import checked_candidate, boundary_passed, MAX_FINAL_BYTES

class Check(unittest.TestCase):
    def response(self):
        value = {'status': 'candidate', 'plan_yaml': '- hosts: localhost', 'rationale': 'Current evidence'}
        return {'model': 'gpt-6-sol', 'status': 'completed', 'usage': {'output_tokens': 40},
            'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}
    def check(self, response):
        return checked_candidate(response, 'gpt-6-sol', 128)
    def test_completed(self):
        self.assertEqual(self.check(self.response())['status'], 'candidate')
    def test_incomplete(self):
        x = self.response(); x['status'] = 'incomplete'
        with self.assertRaises(ValueError): self.check(x)
    def test_tool_call(self):
        x = self.response(); x['output'].append({'type': 'function_call'})
        with self.assertRaises(ValueError): self.check(x)
    def test_unexpected_model(self):
        x = self.response(); x['model'] = 'another-model'
        with self.assertRaises(ValueError): self.check(x)
    def test_missing_usage(self):
        x = self.response(); x.pop('usage')
        with self.assertRaises(ValueError): self.check(x)
    def test_over_budget(self):
        x = self.response(); x['usage']['output_tokens'] = 129
        with self.assertRaises(ValueError): self.check(x)
    def test_unresolved_with_plan(self):
        x = self.response(); s = json.loads(x['output'][0]['content'][0]['text']); s['status'] = 'unresolved'
        x['output'][0]['content'][0]['text'] = json.dumps(s)
        with self.assertRaises(ValueError): self.check(x)

    def test_array_is_not_a_plan(self):
        x = self.response(); x['output'][0]['content'][0]['text'] = '[]'
        with self.assertRaises(ValueError): self.check(x)
    def test_refusal_is_not_a_plan(self):
        x = self.response(); x['output'][0]['content'][0] = {'type': 'refusal', 'refusal': 'No'}
        with self.assertRaises(ValueError): self.check(x)
    def test_null_usage_is_rejected(self):
        x = self.response(); x['usage'] = None
        with self.assertRaises(ValueError): self.check(x)
    def test_byte_bound_is_enforced(self):
        x = self.response(); x['output'][0]['content'][0]['text'] = 'x' * (MAX_FINAL_BYTES + 1)
        with self.assertRaises(ValueError): self.check(x)
    def test_bool_token_count_is_not_integer_usage(self):
        x = self.response(); x['usage']['output_tokens'] = True
        with self.assertRaises(ValueError): self.check(x)
    def test_original_proxy_responses_failure_remains_rejected(self):
        root = Path(__file__).resolve().parents[2]
        x = json.loads((root / 'results/RQ3b-api-pilot-v1/03-token-boundary/response.json').read_text())
        self.assertFalse(boundary_passed(x, 'gpt-6-sol', 16))
    def test_original_proxy_chat_failure_remains_rejected(self):
        root = Path(__file__).resolve().parents[2]
        x = json.loads((root / 'results/RQ3b-api-pilot-v1/05-chat-token-boundary/response.json').read_text())
        self.assertFalse(boundary_passed(x, 'gpt-6-sol', 16, endpoint='chat/completions'))
    def test_truncated_but_over_budget_is_rejected(self):
        x = {'model': 'gpt-6-sol', 'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}, 'usage': {'output_tokens': 17}}
        self.assertFalse(boundary_passed(x, 'gpt-6-sol', 16))

if __name__ == '__main__': unittest.main()

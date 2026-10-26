"""Temporary structural/provenance acceptance; never creates physical trial data."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import prepare_rq3a_registered_responses as registration
import run_rq3a_registered_responses as runner
import reuse_flow_executor as ex


class RegistrationBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='rq3a-structural-test-', dir=ex.ROOT/'results')
        cls.root = Path(cls.temporary.name)
        cls.pilots = cls.root/'synthetic-pilot-fixture'; cls.pilots.mkdir()
        fixture = {
            'independent-acceptance.json': {'accepted': True, 'records': [{}]*5, 'synthetic_fixture': True},
            'final-acceptance.json': {'accepted': True, 'pilots': 5, 'synthetic_fixture': True},
            'unit-acceptance.json': {'accepted': True, 'synthetic_fixture': True},
            'translation-acceptance.json': {'accepted': True, 'constructions': 40, 'synthetic_fixture': True},
            'plan.json': {'synthetic_fixture': True}}
        for name, value in fixture.items():
            (cls.pilots/name).write_text(json.dumps(value))
        independent = fixture['independent-acceptance.json']
        independent['plan_sha256'] = ex.sha(cls.pilots/'plan.json')
        (cls.pilots/'independent-acceptance.json').write_text(json.dumps(independent))
        cls.out = cls.root/'synthetic-protocol-only'
        with contextlib.redirect_stdout(io.StringIO()), patch.object(registration, 'audit_pilots', return_value=independent):
            registration.prepare(cls.out, cls.pilots)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_registration_preserves_matched_queue_and_separate_incident_counter(self):
        p = runner.check(self.out)
        self.assertEqual((p['scenarios'], p['policies'], p['repeats'], len(p['queue'])), (40, 3, 2, 240))
        self.assertEqual(p['main_trials'], 0)
        source = json.loads((ex.ROOT/p['approved_plan']).read_text())
        for actual, original in zip(p['queue'], source['queue']):
            self.assertEqual({k: actual[k] for k in original}, original)
            for role in ('primary', 'replica'):
                child = self.out/actual['name']/role
                protocol = ex.inputs(child)
                self.assertEqual(protocol['trial_identity'], original)
                self.assertEqual(protocol['response_method'], original['method'])
                self.assertTrue(protocol['response_registered'])
                self.assertFalse((child/'live-repair').exists())
                self.assertEqual((child/'tool-snapshot').resolve(), (self.out/'tool-snapshot').resolve())

    def test_registration_cannot_proceed_without_independent_pilot_acceptance(self):
        path = self.pilots/'independent-acceptance.json'
        previous = path.read_text()
        try:
            path.write_text(json.dumps({'accepted': False, 'records': []}))
            with self.assertRaises(AssertionError):
                registration.prepare(self.root/'must-not-register', self.pilots)
            self.assertFalse((self.root/'must-not-register').exists())
        finally:
            path.write_text(previous)

    def test_changed_scientific_source_prevents_launch(self):
        original_sha = ex.sha
        def changed(path):
            if path == ex.ROOT/'tools/main_verifier.py':
                return 'modified'
            return original_sha(path)
        with patch.object(ex, 'sha', changed):
            with self.assertRaisesRegex(AssertionError, 'main_verifier.py'):
                runner.check(self.out)

    def test_receipt_boolean_cannot_replace_actual_pilot_reconstruction(self):
        with patch.object(registration, 'audit_pilots', return_value={'accepted': False}):
            with self.assertRaises(AssertionError):
                registration.prepare(self.root/'must-not-trust-receipt', self.pilots)
        self.assertFalse((self.root/'must-not-trust-receipt').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)

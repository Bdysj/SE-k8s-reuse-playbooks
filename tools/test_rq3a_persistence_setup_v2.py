"""Exercise the observed serialization boundary without issuing live commands."""
import ast
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import run_reuse_flow_development as original
import rq3a_persistence_setup_v2 as amended


class PersistenceBoundaryTests(unittest.TestCase):
    def execute(self, implementation, write_cost=0.025, recover=False, unstable=False):
        clock = [0.0]
        calls = [0]
        # Eleven samples put the saved elapsed time at the actual rejected boundary.
        probe_cost = (59.99769316695165 - 50 - 10 * 0.025) / 11

        def target(row):
            calls[0] += 1
            if calls[0] >= 3:
                clock[0] += probe_cost / 2
            return {'healthy': calls[0] == 1 or recover and calls[0] >= 14}

        def non_target(row):
            if calls[0] >= 3:
                clock[0] += probe_cost / 2
            return {'passed': not (unstable and calls[0] >= 14)}

        def save(path, record):
            if record.get('setup_phase') == 'fault persistence window':
                clock[0] += write_cost

        def sleep(seconds):
            clock[0] += seconds

        with ExitStack() as stack:
            for obj, name, args in [
                (original.time, 'monotonic', {'side_effect': lambda: clock[0]}),
                (original.time, 'time', {'side_effect': lambda: 1000 + clock[0]}),
                (original.time, 'sleep', {'side_effect': sleep}),
                (original.reg, 'reset_s4', {'return_value': None}),
                (original.rb, 'ensure_probe', {'return_value': None}),
                (original.ex, 'save', {'side_effect': save}),
                (original.ex, 'apply_variant', {'return_value': {}}),
                (original.ex, 'inject_current', {'return_value': {}}),
                (original.s, 'healthy', {'return_value': {'passed': True}}),
                (original.v, 'target', {'side_effect': target}),
                (original.v, 'non_target', {'side_effect': non_target}),
                (original.v, 'collect', {'return_value': {}}),
                (original.supported, 'collect', {'return_value': {}}),
                (original, 'confirm_environment', {'return_value': {'passed': True}}),
                (amended, 'confirm_environment', {'return_value': {'passed': True}}),
            ]:
                stack.enter_context(patch.object(obj, name, **args))
            return implementation(
                {'healthy_manifest': 'unused.yaml', 'candidate': 'P1'},
                {'timing': {'healthy_window': 0, 'fault_confirmation': 90}},
                Path('/unused-synthetic-fixture'), {})

    def test_original_reproduces_observed_subthreshold_saved_sample(self):
        setup = self.execute(original.setup)
        self.assertAlmostEqual(setup['fault_persistence'][-1]['elapsed_seconds'],
                               59.99769316695165, places=9)
        self.assertLess(setup['fault_persistence'][-1]['elapsed_seconds'], 60)

    def test_amendment_records_an_extra_observation_before_returning(self):
        old = self.execute(original.setup)
        new = self.execute(amended.setup)
        self.assertEqual(len(new['fault_persistence']), len(old['fault_persistence']) + 1)
        self.assertGreaterEqual(new['fault_persistence'][-1]['elapsed_seconds'], 60)
        self.assertLess(new['fault_persistence'][-2]['elapsed_seconds'], 60)

    def test_long_serialization_cannot_substitute_for_saved_observation(self):
        for cost in (0.001, 0.025, 2, 5, 60):
            with self.subTest(write_seconds=cost):
                new = self.execute(amended.setup, write_cost=cost)
                self.assertGreaterEqual(new['fault_persistence'][-1]['elapsed_seconds'], 60)
                self.assertTrue(all(not x['target']['healthy'] and x['non_target']['passed']
                                    for x in new['fault_persistence']))

    def test_zero_serialization_retains_original_sequence(self):
        self.assertEqual(self.execute(original.setup, write_cost=0),
                         self.execute(amended.setup, write_cost=0))

    def test_extra_sample_still_rejects_self_recovery(self):
        with self.assertRaisesRegex(RuntimeError, 'Fault self-resolved'):
            self.execute(amended.setup, recover=True)

    def test_extra_sample_still_rejects_non_target_instability(self):
        with self.assertRaisesRegex(RuntimeError, 'pre-existing non-target instability'):
            self.execute(amended.setup, unstable=True)

    def test_original92_remains_rejected_by_unchanged_independent_auditor(self):
        from audit_rq3a_response_pilots_v3 import audit_case
        case = original.ex.ROOT / 'results/formal-rq3a-responses-v4/092-P1-1-1-r1-whole_environment'
        audited = audit_case(case)
        self.assertFalse(audited['accepted'])
        self.assertEqual(audited['failures'], ['sixty-second persistent fault period'])
        self.assertTrue(audited['actual_live_execution'])
        self.assertEqual(audited['live_outcome'], 'Successful repair')

    def test_saved_clock_break_is_the_only_setup_behavior_change(self):
        import inspect
        old = inspect.getsource(original.setup)
        expected = old.replace('if time.monotonic()-start>=60:break',
                              "if r['fault_persistence'][-1]['elapsed_seconds']>=60:break")
        self.assertEqual(ast.dump(ast.parse(expected)),
                         ast.dump(ast.parse(inspect.getsource(amended.setup))))
        root = original.ex.ROOT / 'results/formal-rq3a-responses-v4'
        frozen = json.loads((root / 'freeze.json').read_text())
        for name, digest in frozen['source_hashes'].items():
            self.assertEqual(original.ex.sha(original.ex.ROOT / 'tools' / name), digest)


if __name__ == '__main__':
    unittest.main(verbosity=2)

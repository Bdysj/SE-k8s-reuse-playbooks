"""Offline adversarial checks. Fixture seconds are not measured experiment time."""
import copy
import unittest

from controller import Engine, Limits, Plan, digest

HISTORICAL = Plan('P1 fixture', 'unchanged historical bytes', 'historical')

class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now

class Port:
    def __init__(self, clock):
        self.clock = clock
        self.delays = {}
        self.decisions = ['Direct reuse']
        self.live_outcomes = ['Successful repair']
        self.replica_outcomes = ['Successful repair']
        self.calls = []
        self.reconcile_passes = True
        self.fidelity = True
        self.corrupt_primary = False
        self.gate_passes = True
        self.static_passes = True
        self.stale = False
        self.fake_success = False
        self.replica_audit = True
        self.mutate_at_gate = False
        self.cleanup_delay = 0
        self.cleanup_passes = True
        self.presented = []
        self.histories_seen = []

    def step(self, stage, remaining):
        self.calls.append((stage, remaining))
        self.clock.now += self.delays.get(stage, 1)

    def restore(self, identity):
        self.state = {'revision': 0, 'resources': [], 'data_writes': 0}
        self.clock.now = 0
        return {'passed': True, 'start_identity': identity, 'fingerprint': digest(self.state)}

    def healthy_interval(self, seconds): self.clock.now += seconds
    def present(self, incident):
        self.presented.append(incident['id'])
        self.state['revision'] += incident.get('revision_change', 0)
        self.state['fault'] = True
        return {'presented_at': self.clock()}
    def snapshot(self): return copy.deepcopy(self.state)
    def collect(self, remaining):
        self.step('collect', remaining)
        return {'at': self.clock() - (1000 if self.stale else 0),
                'revision': 'r' + str(self.state['revision']), 'contract': 'current-verifier',
                'evidence_id': 'observation-' + str(self.clock())}
    def assess(self, plan, observation, history, remaining):
        self.step('assess', remaining)
        self.histories_seen.append(copy.deepcopy(history))
        return self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]
    def make_replica(self, plan, observation, remaining):
        before = digest(self.state)
        self.step('make_replica', remaining)
        if self.corrupt_primary: self.state['data_writes'] += 1
        return {'fidelity_passed': self.fidelity, 'isolated': True,
                'source_revision': observation['revision'], 'source_contract': observation['contract'],
                'primary_before': before, 'primary_after': digest(self.state)}
    def isolated_replay(self, plan, replica, remaining):
        self.step('isolated_replay', remaining)
        outcome = self.replica_outcomes.pop(0) if len(self.replica_outcomes) > 1 else self.replica_outcomes[0]
        fixed_at = self.clock()
        self.clock.now += self.cleanup_delay
        return {'outcome': outcome, 'executed': True, 'evidence_id': 'isolated-' + str(fixed_at),
                'outcome_fixed_at': fixed_at, 'cleanup_confirmed': self.cleanup_passes}
    def audit_certificate(self, certificate, remaining):
        self.step('audit_certificate', remaining)
        return self.replica_audit
    def plan(self, observation, remaining):
        self.step('plan', remaining)
        # Even identical bytes are generated and count as replanning.
        return Plan('new fixture plan', HISTORICAL.content, 'generated')
    def static_check(self, plan, remaining):
        self.step('static_check', remaining)
        return self.static_passes
    def safety_gate(self, plan, observation, remaining):
        self.step('safety_gate', remaining)
        if self.mutate_at_gate: self.state['revision'] += 1
        return self.gate_passes
    def execute(self, plan, permission, remaining):
        assert permission['candidate_sha256'] == plan.sha256
        assert permission['revision'] == 'r' + str(self.state['revision'])
        assert self.clock() <= permission['expires_at']
        self.step('execute', remaining)
        self.state['resources'].append('repair-' + str(len(self.state['resources'])))
        self.state['data_writes'] += 1
        return {'executed': True, 'complete': True}
    def verify(self, plan, execution, remaining):
        self.step('verify', remaining)
        outcome = self.live_outcomes.pop(0) if len(self.live_outcomes) > 1 else self.live_outcomes[0]
        self.state['fault'] = outcome != 'Successful repair'
        return {'outcome': outcome, 'evidence_id': 'current-' + str(self.clock()),
                'target_recovered': outcome == 'Successful repair',
                'non_target_passed': not self.fake_success, 'window_complete': True}
    def reconcile(self, plan, execution, remaining):
        self.step('reconcile', remaining)
        return self.reconcile_passes

class Checks(unittest.TestCase):
    def setup_engine(self, **overrides):
        clock = Clock(); port = Port(clock)
        limits = Limits(**dict(response_cap=100, evidence_ttl=30, refreshes=1, replays=1,
                               generated_attempts=2, assessments=4, **overrides))
        return Engine(port, clock, limits, HISTORICAL), port
    def run_one(self, engine, process='proposed', incidents=None):
        return engine.run_sequence(process, 'fixture-sequence', 'same-start', incidents or [{'id': 'i1'}])
    def test_all_processes_recover_through_shared_gate(self):
        for process in ('proposed', 'replanning', 'full_revalidation'):
            engine, port = self.setup_engine()
            result = self.run_one(engine, process)
            self.assertEqual(result['recovered_incidents'], 1)
            self.assertIn('safety_gate', [c[0] for c in port.calls])
    def test_replanning_runs_on_every_incident_even_with_success_history(self):
        engine, port = self.setup_engine()
        result = self.run_one(engine, 'replanning', [{'id':'i1'}, {'id':'i2'}])
        self.assertEqual(sum(c[0] == 'plan' for c in port.calls), 2)
        self.assertEqual(result['records'][1]['live_attempts'][0]['plan_origin'], 'generated')
    def test_identical_generated_bytes_are_not_direct_reuse(self):
        engine, port = self.setup_engine(); result = self.run_one(engine, 'replanning')
        self.assertEqual(result['records'][0]['live_attempts'][0]['route'], 'generated repair')
    def test_full_revalidation_runs_even_without_a_configuration_change(self):
        engine, port = self.setup_engine(); result = self.run_one(engine, 'full_revalidation')
        self.assertEqual(result['records'][0]['replays'], 1)
    def test_preserved_resources_data_and_history_between_incidents(self):
        engine, port = self.setup_engine(); result = self.run_one(engine, incidents=[{'id':'i1'}, {'id':'i2'}])
        self.assertEqual(port.state['data_writes'], 2)
        self.assertEqual(len(port.state['resources']), 2)
        self.assertEqual(len(port.histories_seen[1]), 1)
        self.assertNotEqual(result['records'][0]['state_after_sha256'], result['records'][1]['state_after_sha256'])
    def test_history_and_application_state_reset_between_sequences(self):
        engine, port = self.setup_engine(); self.run_one(engine); result = self.run_one(engine)
        self.assertEqual(port.state['data_writes'], 1)
        self.assertEqual(len(result['history']), 1)
        self.assertEqual(port.histories_seen[-1], ())
    def test_stop_reuse_uses_same_generated_fallback(self):
        engine, port = self.setup_engine(); port.decisions = ['Stop reuse']
        result = self.run_one(engine)
        self.assertEqual(result['records'][0]['generated_attempts'], 1)
        self.assertTrue(result['records'][0]['recovered'])
    def test_failed_isolated_replay_is_not_an_invalid_live_action(self):
        engine, port = self.setup_engine(); port.replica_outcomes = ['Confirmed failure']
        result = self.run_one(engine, 'full_revalidation')['records'][0]
        self.assertEqual(result['isolated_attempts'][0]['outcome'], 'Confirmed failure')
        self.assertEqual(result['incidents_with_invalid_live_execution'], 0)
        self.assertEqual(result['live_attempts'][0]['plan_origin'], 'generated')
    def test_failed_live_reuse_requires_reconcile_before_fallback(self):
        engine, port = self.setup_engine(); port.live_outcomes = ['Confirmed failure','Successful repair']
        result = self.run_one(engine)['records'][0]
        self.assertEqual(len(result['live_attempts']), 2)
        stages = [x[0] for x in port.calls]
        self.assertLess(stages.index('reconcile'), stages.index('plan'))
        self.assertEqual(result['incidents_with_invalid_live_execution'], 1)
    def test_uncertain_unsafe_reconcile_blocks_next_action_and_incident(self):
        engine, port = self.setup_engine(); port.live_outcomes = ['Indeterminate']; port.reconcile_passes = False
        result = self.run_one(engine, incidents=[{'id':'i1'}, {'id':'i2'}])
        self.assertEqual(len(result['records'][0]['live_attempts']), 1)
        self.assertEqual(result['unpresented_incidents'], 1)
        self.assertEqual(port.presented, ['i1'])
    def test_generated_retry_is_bounded_and_retains_failed_attempts(self):
        engine, port = self.setup_engine(); port.live_outcomes = ['Confirmed failure']
        result = self.run_one(engine, 'replanning')['records'][0]
        self.assertEqual(result['generated_attempts'], 2)
        self.assertEqual(len(result['live_attempts']), 2)
        self.assertEqual(result['capped_recovery_seconds'], 100)
        self.assertTrue(result['censored'])
    def test_planning_timeout_cannot_restart_incident_clock(self):
        engine, port = self.setup_engine(); port.delays['plan'] = 101
        result = self.run_one(engine, 'replanning')['records'][0]
        self.assertEqual(result['live_attempts'], [])
        self.assertEqual(result['capped_recovery_seconds'], 100)
        self.assertEqual(result['observed_handling_seconds'], 102)
    def test_late_execution_stays_indeterminate(self):
        engine, port = self.setup_engine(); port.delays['execute'] = 101
        result = self.run_one(engine)['records'][0]
        self.assertEqual(result['live_attempts'][0]['outcome'], 'Indeterminate')
        self.assertFalse(result['recovered'])
        self.assertNotIn('plan', [x[0] for x in port.calls])
    def test_non_target_failure_cannot_be_overridden_by_success_label(self):
        engine, port = self.setup_engine(); port.fake_success = True; port.reconcile_passes = False
        result = self.run_one(engine)['records'][0]
        self.assertFalse(result['recovered'])
        self.assertEqual(result['live_attempts'][0]['outcome'], 'Indeterminate')
    def test_stale_observations_cannot_launch(self):
        engine, port = self.setup_engine(); port.stale = True
        result = self.run_one(engine)['records'][0]
        self.assertEqual(result['live_attempts'], [])
        self.assertEqual(result['refreshes'], 1)
    def test_expired_certificate_never_launches_historical_plan(self):
        engine, port = self.setup_engine(); port.delays['audit_certificate'] = 31
        result = self.run_one(engine, 'full_revalidation')['records'][0]
        self.assertTrue(all(x['plan_origin'] != 'historical' for x in result['live_attempts']))
    def test_bad_fidelity_prevents_isolated_replay(self):
        engine, port = self.setup_engine(); port.fidelity = False
        result = self.run_one(engine, 'full_revalidation')['records'][0]
        self.assertEqual(result['isolated_attempts'], [])
        self.assertEqual(result['live_attempts'][0]['plan_origin'], 'generated')
    def test_cleanup_time_does_not_rejuvenate_validation_certificate(self):
        engine, port = self.setup_engine(); port.cleanup_delay = 31
        result = self.run_one(engine, 'full_revalidation')['records'][0]
        self.assertTrue(all(x['plan_origin'] != 'historical' for x in result['live_attempts']))
    def test_unconfirmed_replica_cleanup_blocks_live_fallback(self):
        engine, port = self.setup_engine(); port.cleanup_passes = False
        result = self.run_one(engine, 'full_revalidation')['records'][0]
        self.assertEqual(result['live_attempts'], [])
    def test_replica_primary_contamination_is_rejected(self):
        engine, port = self.setup_engine(); port.corrupt_primary = True
        result = self.run_one(engine, 'full_revalidation')['records'][0]
        self.assertEqual(result['isolated_attempts'], [])
        self.assertEqual(result['live_attempts'], [])
    def test_provenance_failure_never_launches_historical_plan(self):
        engine, port = self.setup_engine(); port.replica_audit = False
        result = self.run_one(engine, 'full_revalidation')['records'][0]
        self.assertEqual(result['live_attempts'][0]['plan_origin'], 'generated')
    def test_change_during_gate_blocks_stale_launch(self):
        engine, port = self.setup_engine(); port.mutate_at_gate = True
        result = self.run_one(engine)['records'][0]
        self.assertEqual(result['live_attempts'], [])
    def test_healthy_intervals_age_evidence_but_are_excluded_from_recovery(self):
        engine, port = self.setup_engine()
        result = self.run_one(engine, incidents=[{'id':'i1'}, {'id':'i2','healthy_interval_seconds':500}])
        self.assertEqual(result['records'][0]['capped_recovery_seconds'], result['records'][1]['capped_recovery_seconds'])
        self.assertGreater(result['records'][1]['presented_at'], 500)
    def test_shared_budget_is_passed_to_every_incident_stage(self):
        engine, port = self.setup_engine(); self.run_one(engine, 'full_revalidation')
        budgets = [x[1] for x in port.calls]
        self.assertEqual(budgets, sorted(budgets, reverse=True))
        self.assertTrue(all(0 < x <= 100 for x in budgets))
    def test_formal_execution_rejected_before_adapter_call(self):
        clock = Clock(); port = Port(clock)
        with self.assertRaises(ValueError):
            Engine(port,clock,Limits(100,30,1,1,2,4),HISTORICAL,mode='formal')
        self.assertEqual(port.calls, [])
    def test_boolean_or_nonfinite_budgets_rejected(self):
        for cap in (True, float('nan'), float('inf'), 0, -1):
            with self.assertRaises(ValueError): Limits(cap,30,1,1,2,4)

if __name__ == '__main__':
    unittest.main()

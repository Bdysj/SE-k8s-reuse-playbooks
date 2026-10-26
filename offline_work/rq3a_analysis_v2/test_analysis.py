"""Adversarial denominator, pairing, uncertainty and retention checks."""
import copy
import unittest

from analysis import bounds, build, matched_groups, paired_comparisons, quantile, rate, summarize

def row(method='proposed', scenario='P1-1-1', repeat=1, decision='Direct reuse', outcome='Successful repair', **kw):
    result = {'method': method, 'scenario':scenario, 'candidate':scenario.split('-')[0], 'repeat':repeat,
              'initial_decision':decision, 'outcome':outcome, 'executed':outcome!='Not executed',
              'isolated_executed':False, 'isolated_outcome':None, 'independent_replay_outcome':'Successful repair'}
    result.update(kw)
    return result

class Checks(unittest.TestCase):
    def test_empty_denominator_is_not_applicable(self):
        self.assertIsNone(rate(0,0))
        self.assertIsNone(summarize([])['conditional_invalid_direct_rate'])
        self.assertIsNone(bounds(0,0,0)['invalid_upper'])
    def test_revalidation_success_is_excluded_from_direct_denominator(self):
        result=summarize([row(outcome='Confirmed failure'),row(decision='Revalidate')])
        self.assertEqual(result['determinate_initial_direct_denominator'],1)
        self.assertEqual(result['conditional_invalid_direct_rate'],1)
        self.assertEqual(result['after_initial_revalidation']['actual_live_executions'],1)
    def test_not_executed_is_not_failure(self):
        result=summarize([row(outcome='Not executed'),row(decision='Stop reuse',outcome='Not executed')])
        self.assertEqual(result['all_live_outcomes']['Confirmed failure'],0)
        self.assertEqual(result['actual_live_executions'],0)
        self.assertEqual(result['eligible_accepted_incidents'],2)
    def test_launch_refusal_separate_from_decision_and_execution(self):
        result=summarize([row(outcome='Not executed',launch_blocked=True)])
        self.assertEqual(result['physical_launch_refusals'],1)
        self.assertEqual(result['initial_decisions']['Direct reuse'],1)
        self.assertEqual(result['initial_direct_live_executions'],0)
    def test_indeterminate_preserved_with_extreme_bounds(self):
        result=summarize([row(),row(outcome='Confirmed failure'),row(outcome='Indeterminate')])
        self.assertEqual(result['determinate_initial_direct_denominator'],2)
        self.assertEqual(result['conditional_invalid_direct_rate'],.5)
        self.assertAlmostEqual(result['direct_missing_outcome_bounds']['invalid_lower'],1/3)
        self.assertAlmostEqual(result['direct_missing_outcome_bounds']['invalid_upper'],2/3)
        self.assertAlmostEqual(result['all_live_missing_outcome_bounds']['outcome_evidence_coverage'],2/3)
    def test_incident_and_conditional_rates_have_different_denominators(self):
        result=summarize([row(outcome='Confirmed failure'),row(decision='Stop reuse',outcome='Not executed')])
        self.assertEqual(result['conditional_invalid_direct_rate'],1)
        self.assertEqual(result['invalid_direct_per_eligible_incident'],.5)
    def test_isolated_failure_not_counted_as_live_failure(self):
        result=summarize([row(outcome='Not executed',decision='Revalidate',isolated_executed=True,isolated_outcome='Confirmed failure')])
        self.assertEqual(result['isolated_outcomes']['Confirmed failure'],1)
        self.assertEqual(result['all_live_outcomes']['Confirmed failure'],0)
    def test_opportunity_uses_independent_replay_not_live_success(self):
        result=summarize([row(decision='Stop reuse',outcome='Not executed'),
                          row(independent_replay_outcome='Confirmed failure')])
        self.assertEqual(result['independent_successful_replay_opportunities'],1)
        self.assertEqual(result['retained_initial_direct_opportunity_rate'],0)
    def test_duplicate_method_cannot_inflate_paired_block(self):
        with self.assertRaises(ValueError):matched_groups([row(),row()])
    def test_incomplete_block_excluded_from_matched_comparison(self):
        rows=[row(method=m) for m in ('proposed','fault_matching','whole_environment')]+[row(scenario='P2-1-1')]
        result=paired_comparisons(rows)
        self.assertEqual(result['complete_matched_blocks'],1)
        self.assertEqual(result['incomplete_matched_blocks'],1)
        self.assertFalse(result['intervals_computed'])
    def test_incomplete_collection_cannot_compute_final_intervals(self):
        with self.assertRaises(ValueError):paired_comparisons([row()],infer=True)
    def test_percentile_interpolation(self):
        self.assertEqual(quantile([0,10],.5),5)
        self.assertIsNone(quantile([],.95))
    def test_all_attempt_sensitivity_retains_rejected_success_and_uncertainty(self):
        accepted=[row()];extras=[row(outcome='Indeterminate'),row(outcome='Successful repair',decision='Revalidate')]
        result=summarize(accepted+extras)
        self.assertEqual(result['actual_live_executions'],3)
        self.assertEqual(result['all_live_outcomes']['Indeterminate'],1)
        self.assertEqual(result['all_live_outcomes']['Successful repair'],2)
    def test_final_resampling_preserves_clustered_paired_design(self):
        rows=[]
        for candidate in range(1,6):
            for scenario in range(1,9):
                for repeat in (1,2):
                    for method in ('proposed','fault_matching','whole_environment'):
                        rows.append(row(method=method,scenario=f'P{candidate}-{scenario}-1',repeat=repeat))
        result=paired_comparisons(rows,infer=True,replicates=1000,seed=42)
        self.assertEqual(result['complete_matched_blocks'],80)
        self.assertEqual(result['complete_matched_scenarios'],40)
        interval=result['comparisons']['fault_matching']['conditional_invalid_direct_rate']['uncertainty']
        self.assertEqual(interval['valid_replicates'],1000)
        self.assertTrue(interval['degenerate'])
        self.assertIn('cannot establish general reliability',interval['claim'])
        self.assertEqual(result['resampling']['unit'],'scenario within fixed playbook')
    def test_input_rows_not_mutated(self):
        rows=[row()];before=copy.deepcopy(rows);summarize(rows);paired_comparisons(rows)
        self.assertEqual(rows,before)
    def test_predecision_setup_rejection_has_no_invented_policy_decision(self):
        accepted=[row(method=m) for m in ('proposed','fault_matching','whole_environment')]
        extra=row(method='fault_matching',decision=None,outcome='Not executed',executed=False)
        snapshot={'rows':accepted,'extra_rows':[extra],'completed_at_snapshot':3,
                  'rejected_entries':1,'deduplicated_rejections':[]}
        result=build(snapshot)
        summary=result['all_unique_physical_attempts_sensitivity']['fault_matching']
        self.assertEqual(summary['included_collection_attempts'],2)
        self.assertEqual(summary['predecision_setup_rejections'],1)
        self.assertEqual(summary['policy_stage_attempts_including_unaccepted'],1)
        self.assertEqual(summary['initial_decisions']['Direct reuse'],1)
        self.assertEqual(summary['conditional_invalid_direct_rate'],0)
        self.assertEqual(summary['all_live_outcomes']['Confirmed failure'],0)
        self.assertEqual(result['per_method']['fault_matching']['eligible_accepted_incidents'],1)
    def test_physical_execution_with_missing_decision_cannot_disappear_from_sensitivity(self):
        snapshot={'rows':[row()],'extra_rows':[row(decision=None,outcome='Successful repair')],
                  'completed_at_snapshot':1,'rejected_entries':1,'deduplicated_rejections':[]}
        with self.assertRaisesRegex(ValueError,'actual execution'):
            build(snapshot)

if __name__ == '__main__':unittest.main()

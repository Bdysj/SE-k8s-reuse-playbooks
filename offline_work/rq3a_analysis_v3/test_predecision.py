import copy
import unittest
from analysis import predecision_rejection

class SetupRejectionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.outer={'passed':False,'primary_post_reset_confirmed':True,'error':'Healthy gate rejected'}
        self.setup={'setup_phase':'healthy observation window',
                    'healthy_window':[{'target':{'healthy':False},'non_target':{'passed':True}}]}
        self.kw={'live_record_exists':False,'isolated_record_exists':False,'replica_request_exists':False}

    def call(self):return predecision_rejection(self.outer,self.setup,**self.kw)

    def test_pre_fault_rejection_preserves_absent_initial_decision(self):
        result=self.call();self.assertIsNone(result['initial_decision'])
        self.assertFalse(result['decision_stage_reached']);self.assertFalse(result['fault_injected_before_rejection'])

    def test_post_injection_predecision_rejection_kept_distinct(self):
        self.outer['error']='Fault self-resolved or pre-existing non-target instability'
        self.setup={'setup_phase':'fault persistence window','fault_persistence':[
            {'target':{'healthy':False},'non_target':{'passed':False}}]}
        result=self.call();self.assertTrue(result['fault_injected_before_rejection']);self.assertIsNone(result['initial_decision'])

    def test_already_injected_fault_not_hidden_in_healthy_setup(self):
        self.setup['fault_activation_at']=1
        with self.assertRaises(ValueError):self.call()

    def test_missing_failure_observation_rejected(self):
        self.setup['healthy_window'][0]['target']['healthy']=True
        with self.assertRaises(ValueError):self.call()

    def test_actual_execution_cannot_be_excluded_as_setup_failure(self):
        for key in self.kw:
            self.kw[key]=True
            with self.assertRaises(ValueError):self.call()
            self.kw[key]=False

    def test_unconfirmed_reset_or_unknown_phase_fails_closed(self):
        self.outer['primary_post_reset_confirmed']=False
        with self.assertRaises(ValueError):self.call()
        self.outer['primary_post_reset_confirmed']=True;self.setup['setup_phase']='outcome window'
        with self.assertRaises(ValueError):self.call()

if __name__=='__main__':unittest.main()

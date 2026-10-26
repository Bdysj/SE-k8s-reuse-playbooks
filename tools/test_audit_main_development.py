import copy
import unittest

import audit_main_development as audit

TIMING={'outcome_window':210,'pre_action':60,'repair_budget':120,'sustained_recovery':30,
        'pre_check_max_age':30,'launch_after_pre_check_max_seconds':5}


def fixture():
    return {'type':'pre-main_executor_development','main_trials':0,'registered':False,
        'candidate':'P2','arm':'no-repair','completed_at':1000,'executor_evidence_complete':True,
        'observation_complete':True,'post_reset_confirmed':True,'outcome_fixed_at':900,
        'setup_checks':[{'passed':True}],
        'healthy_window':[{'target':{'healthy':True},'non_target':{'passed':True}}],
        'fault_confirmation':[{'healthy':False}],
        'samples':[{'healthy':False,'window_seconds':0},{'healthy':False,'window_seconds':210}],
        'window_seconds':211,'action_executed':False,'execution_complete':None,
        'no_repair_persistence_confirmed':True,'pre_action_non_target':[{'passed':True}]}


class AuditTests(unittest.TestCase):
    def test_full_no_repair_evidence_is_accepted(self):
        self.assertTrue(audit.trace_checks(fixture(),TIMING)['accepted'])

    def test_success_flag_cannot_hide_missing_window_or_reset(self):
        for key,value in [('observation_complete',False),('post_reset_confirmed',False),
                          ('window_seconds',100),('collection_or_setup_error','timeout')]:
            r=fixture();r[key]=value
            self.assertFalse(audit.trace_checks(r,TIMING)['accepted'],key)

    def test_self_resolution_cannot_pass_no_repair(self):
        r=fixture();r['samples'][-1]['healthy']=True
        self.assertFalse(audit.trace_checks(r,TIMING)['accepted'])

    def test_missing_non_target_data_is_not_persistent_fault_acceptance(self):
        r=fixture();r['pre_action_non_target']=[]
        self.assertFalse(audit.trace_checks(r,TIMING)['accepted'])


if __name__=='__main__':unittest.main(verbosity=2)

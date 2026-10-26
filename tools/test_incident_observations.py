import copy
import unittest

import incident_observations as i


class IncidentTests(unittest.TestCase):
    def test_construction_and_outcome_cannot_enter_text(self):
        r={'repair_start_seconds':60,'samples':[{'window_seconds':1,'status':502,'curl_rc':0,
             'rollout':{'complete':False,'desired':0}}],
           'construction':{'required_replicas':4,'injected_fault':'secret-label'},
           'repair_outcome':'Successful repair','expected_effect':'failure'}
        result=i.before_action(r,'ratings')
        changed=copy.deepcopy(r);changed['repair_outcome']='Confirmed failure';changed['construction']={}
        self.assertEqual(result,i.before_action(changed,'ratings'))
        self.assertNotIn('4',str(result['incident']))
        self.assertNotIn('secret-label',str(result))

    def test_post_action_symptom_is_not_selected(self):
        r={'action_start_seconds':60,'samples':[{'window_seconds':1,'status':502},
                                              {'window_seconds':61,'status':200}]}
        self.assertEqual(i.before_action(r,'ratings')['incident']['error_codes'],['HTTP 502'])

    def test_no_pre_action_data_cannot_be_fabricated(self):
        for r in ({'samples':[]},{'action_start_seconds':60,'samples':[{'window_seconds':61,'status':200}]}):
            with self.assertRaises(ValueError):i.before_action(r,'ratings')

    def test_healthy_http_is_not_failed_by_stricter_outcome_flag(self):
        sample={'status':200,'curl_rc':0,'healthy':False,'workload':{'healthy':True,'desired':1}}
        self.assertEqual(i.observed_incident(sample,'ratings')['symptoms'],[])


if __name__=='__main__':unittest.main(verbosity=2)

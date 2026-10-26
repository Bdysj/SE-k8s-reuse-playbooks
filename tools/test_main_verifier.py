"""Adversarial owner-binding and outcome-label checks (no cluster mutations)."""
import copy
import unittest

import main_verifier as v


def fixture():
    t = {'metadata':{'labels':{'service':'web'}}, 'spec':{'containers':[{'name':'web','image':'fixed'}]}}
    d = {'metadata':{'name':'web','uid':'d-new','generation':2}, 'spec':{'replicas':1,'template':copy.deepcopy(t)},
         'status':{'observedGeneration':2,'updatedReplicas':1,'readyReplicas':1,'availableReplicas':1}}
    rs = {'metadata':{'name':'web-current','uid':'rs-new','ownerReferences':[{'kind':'Deployment','uid':'d-new','controller':True}]},'spec':{'template':copy.deepcopy(t)}}
    p = {'metadata':{'name':'web-current-1','uid':'p-new','labels':{'service':'web'},
                     'ownerReferences':[{'kind':'ReplicaSet','uid':'rs-new','controller':True}]},
         'status':{'containerStatuses':[{'ready':True,'restartCount':0}]}}
    sl = {'metadata':{'name':'ep','labels':{'kubernetes.io/service-name':'web'}},
          'endpoints':[{'conditions':{'ready':True},'targetRef':{'kind':'Pod','uid':'p-new'}}]}
    return {'deployments':[d], 'statefulsets':[], 'replicasets':[rs], 'pods':[p],
            'services':[{'metadata':{'name':'web'},'spec':{'selector':{'service':'web'}}}], 'endpointslices':[sl]}


class VerifierTests(unittest.TestCase):
    def test_ready_current_owner(self):
        self.assertTrue(v.workload(fixture(),'web')['healthy'])
        self.assertTrue(v.endpoint_binding(fixture(),'web','web')['healthy'])

    def test_matching_label_does_not_rescue_wrong_owner(self):
        data=fixture();data['pods'][0]['metadata']['ownerReferences'][0]['uid']='foreign-rs'
        self.assertFalse(v.workload(data,'web')['healthy'])
        self.assertFalse(v.endpoint_binding(data,'web','web')['healthy'])

    def test_old_template_serving_200_cannot_complete_new_rollout(self):
        data=fixture();data['deployments'][0]['spec']['template']['spec']['containers'][0]['image']='new-fixed'
        self.assertFalse(v.workload(data,'web')['healthy'])

    def test_empty_and_null_endpoint_lists_fail_cleanly(self):
        for endpoints in (None,[]):
            data=fixture();data['endpointslices'][0]['endpoints']=endpoints
            self.assertFalse(v.endpoint_binding(data,'web','web')['healthy'])

    def test_missing_declared_workload_fails(self):
        self.assertFalse(v.workload(fixture(),'deleted-workload')['healthy'])

    def test_retiring_old_pod_allowed_only_with_healthy_current_rollout(self):
        data=fixture();old=copy.deepcopy(data['pods'][0]);old['metadata'].update(uid='p-old',deletionTimestamp='now')
        old['status']['containerStatuses'][0]['ready']=False;data['pods'].append(old)
        self.assertTrue(v.workload(data,'web')['healthy'])
        data['pods'][0]['status']['containerStatuses'][0]['ready']=False
        self.assertFalse(v.workload(data,'web')['healthy'])

    def test_restarts_and_stale_generation_fail(self):
        data=fixture();data['pods'][0]['status']['containerStatuses'][0]['restartCount']=1
        self.assertFalse(v.workload(data,'web')['healthy'])
        data=fixture();data['deployments'][0]['status']['observedGeneration']=1
        self.assertFalse(v.workload(data,'web')['healthy'])

    def test_preexisting_failure_not_new_regression(self):
        pre={'checks':{'shipping':{'healthy':False}}}
        self.assertIsNone(v.regression(pre,[pre],True)[0])

    def test_confirmed_new_regression_survives_incomplete_window(self):
        pre={'checks':{'shipping':{'healthy':True}}};post={'checks':{'shipping':{'healthy':False}}}
        self.assertEqual(v.regression(pre,[post],False),(True,['shipping']))

    def test_missing_named_check_is_unknown_not_success(self):
        pre={'checks':{'shipping':{'healthy':True}}}
        self.assertIsNone(v.regression(pre,[{'checks':{}}],True)[0])

    def test_outcome_retains_confirmed_failure_with_missing_flags(self):
        self.assertEqual(v.outcome(False,None,None),'Confirmed failure')
        self.assertEqual(v.outcome(True,None,False),'Indeterminate')
        self.assertEqual(v.outcome(None,None,True),'Confirmed failure')
        self.assertEqual(v.outcome(True,True,False),'Successful repair')
        self.assertEqual(v.outcome(None,None,None,False),'Not executed')


if __name__=='__main__':
    unittest.main(verbosity=2)

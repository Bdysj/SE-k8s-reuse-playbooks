"""Predeclared RQ2 rule checks, with synthetic observations, never outcome evidence."""
import copy
import unittest

import reuse_assessor as a
from test_main_verifier import fixture


def states(*values):
    return [{'condition':c,'state':s} for c,s in zip(a.CONDITIONS,values)]


def inputs():
    snapshot=fixture()
    snapshot.update(started_at=90,completed_at=95,p1_discovery_names=['web'],relevant_revision='revision-A',client_frontend_service='web')
    current={'source':'prospective-test-annotation','source_sha256':'fixed-annotation',
             'serving_deployment':'web','frontend_service':'web','catalogue_service':'catalogue',
             'required_replicas':1,'cart_check':False,'vote_check':False,'target_path':'/',
             'equivalent_endpoints':{'P1':['/','/index.html']}}
    return snapshot,current


class AssessorTests(unittest.TestCase):
    def test_all_supported_direct(self):
        self.assertEqual(a.decision(states(a.SAT,a.SAT,a.SAT,a.SAT))['decision'],'Direct reuse')

    def test_contradiction_precedes_unknown(self):
        self.assertEqual(a.decision(states(a.UNKNOWN,a.SAT,a.CONTRA,a.SAT))['decision'],'Stop reuse')

    def test_missing_and_stale_revalidate(self):
        for mode in ('missing','stale','future','long_collection'):
            snapshot,current=inputs()
            if mode=='missing':snapshot.pop('services')
            if mode=='stale':snapshot['started_at']=1
            if mode=='future':snapshot['started_at']=101
            if mode=='long_collection':snapshot['started_at']=10;snapshot['completed_at']=100
            r=a.assess('P1','fixed',snapshot,current,now=100)
            self.assertEqual(r['decision'],'Revalidate',mode)
            self.assertTrue(all(x['state']==a.UNKNOWN for x in r['states']))

    def test_budget_exhaustion_stops(self):
        self.assertEqual(a.decision(states(a.SAT,a.UNKNOWN,a.SAT,a.SAT),0)['decision'],'Stop reuse')

    def test_ablation_omits_rather_than_marks_unknown(self):
        s=states(a.CONTRA,a.SAT,a.SAT,a.SAT)
        self.assertEqual(a.decision(s,omitted='target_objects')['decision'],'Direct reuse')

    def test_empty_locator_is_contradiction_missing_observation_is_unknown(self):
        snapshot,current=inputs();snapshot['p1_discovery_names']=[]
        self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100)['decision'],'Stop reuse')
        snapshot.pop('p1_discovery_names')
        self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100)['decision'],'Revalidate')

    def test_logical_replacement_allowed_idle_target_rejected(self):
        snapshot,current=inputs()
        self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100)['decision'],'Direct reuse')
        current['serving_deployment']='frontend-live'
        self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100)['decision'],'Stop reuse')

    def test_stricter_verifier_alone_needs_revalidation_not_failure(self):
        snapshot,current=inputs();current['cart_check']=True
        self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100)['decision'],'Revalidate')

    def test_revalidation_needs_fidelity_freshness_and_exact_contract(self):
        snapshot,current=inputs();current['cart_check']=True
        validation={'candidate_sha256':'fixed','contract_sha256':a.digest(current),'fidelity_passed':True,
                    'outcome':'Successful repair','completed_at':99,'relevant_revision':'revision-A'}
        self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100,validation=validation)['decision'],'Direct reuse')
        for k,value in [('fidelity_passed',False),('completed_at',1),('contract_sha256','other'),('relevant_revision','other')]:
            bad=copy.deepcopy(validation);bad[k]=value
            self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100,validation=bad)['decision'],'Revalidate',k)
        validation['outcome']='Confirmed failure'
        self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100,validation=validation)['decision'],'Stop reuse')

    def test_change_after_assessment_requires_reassessment(self):
        snapshot,current=inputs();r=a.assess('P1','fixed',snapshot,current,now=100)
        self.assertTrue(a.before_execution(r,'revision-A')['permitted'])
        self.assertEqual(a.before_execution(r,'revision-B')['next_action'],'reassess')

    def test_verifier_change_does_not_hide_overlapping_contradiction(self):
        snapshot,current=inputs();current.update(cart_check=True,serving_deployment='frontend-live')
        self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100)['decision'],'Stop reuse')

    def test_role_annotation_alone_does_not_prove_current_binding(self):
        snapshot,current=inputs();snapshot['services'][0]['spec']['selector']['service']='other'
        self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100)['decision'],'Stop reuse')
        snapshot,current=inputs();snapshot.pop('client_frontend_service')
        self.assertEqual(a.assess('P1','fixed',snapshot,current,now=100)['decision'],'Revalidate')


if __name__=='__main__':
    unittest.main(verbosity=2)

"""Prospective adversarial flow checks; synthetic rules and archived pilot traces.

These checks establish implementation consistency only. They are not live repair
outcomes, calibration accuracy, or main experimental trials.
"""
import copy
import json
import unittest
from unittest.mock import patch

import reuse_assessor as a
import reuse_workflow_v2 as w
import test_reuse_workflow as legacy
from test_reuse_assessor import inputs
from reuse_validation import outcome_checks, normalise_service_ports
from reset_baseline import ROOT
from prepare_reuse_flow_development import can_carry, rejected_boundaries


class ExistingWorkflowRegressions(legacy.WorkflowTests):
    def setUp(self):
        self.replacement=patch.object(legacy,'w',w);self.replacement.start()
    def tearDown(self):self.replacement.stop()


class NewBoundaryTests(unittest.TestCase):
    def test_failed_healthy_setup_retains_observations_before_fault_or_action(self):
        import tempfile
        from pathlib import Path
        import run_reuse_flow_development as controller
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            with patch.object(controller.reg,'reset_s4'), patch.object(controller.rb,'ensure_probe'), \
                 patch.object(controller.ex,'apply_variant',return_value={}), \
                 patch.object(controller.s,'healthy',return_value={'passed':True}), \
                 patch.object(controller.v,'target',return_value={'healthy':False,'status':0,'curl_rc':28}), \
                 patch.object(controller.v,'non_target',return_value={'passed':True}), \
                 patch.object(controller.ex,'inject_current') as inject:
                with self.assertRaisesRegex(RuntimeError,'Healthy gate rejected'):
                    controller.setup({'healthy_manifest':'unused'}, {'timing':{'healthy_window':15}},directory,{})
                r=json.loads((directory/'incident-setup.json').read_text())
                self.assertEqual(r['setup_phase'],'healthy observation window')
                self.assertEqual(r['healthy_window'][0]['target']['curl_rc'],28)
                self.assertTrue(r['healthy_window'][0]['non_target']['passed'])
                inject.assert_not_called()

    def test_carry_requires_passed_record_same_case_and_same_runtime(self):
        root=ROOT/'results/reuse-flow-development-v2';plan=json.loads((root/'plan.json').read_text())
        case=copy.deepcopy(plan['cases'][0])
        self.assertTrue(can_carry(root/case['name'],case,plan))
        changed=copy.deepcopy(case);changed['configuration']['observation_ttl']=999
        self.assertFalse(can_carry(root/case['name'],changed,plan))
        changed=copy.deepcopy(case);changed['environment_profile']={'name':'new baseline'}
        self.assertFalse(can_carry(root/case['name'],changed,plan))
        previous=copy.deepcopy(plan);previous['source_hashes']['reuse_flow_executor.py']='changed'
        self.assertFalse(can_carry(root/case['name'],case,previous))
        import prepare_reuse_flow_development as prepare
        with patch.object(prepare,'SOURCES',prepare.SOURCES+['new_runtime_method.py']):
            self.assertFalse(can_carry(root/case['name'],case,plan))
        rejected=copy.deepcopy(plan['cases'][2])
        self.assertFalse(can_carry(root/rejected['name'],rejected,plan))
        carried_root=ROOT/'results/reuse-flow-development-v3'
        carried_plan=json.loads((carried_root/'plan.json').read_text())
        self.assertTrue(can_carry(carried_root/case['name'],case,carried_plan))

    def test_unused_replica_is_distinct_from_used_and_unconfirmed_replica(self):
        used=ROOT/'results/reuse-flow-development-v2/03-failed-validation-P3/acceptance.json'
        unused=ROOT/'results/reuse-flow-development-v3/03-failed-validation-P3/acceptance.json'
        self.assertEqual(rejected_boundaries(used)['replica_boundary'],'reset confirmed')
        self.assertTrue(rejected_boundaries(unused)['replica_boundary'].startswith('unused;'))
        import tempfile
        from pathlib import Path
        import prepare_reuse_flow_development as prepare
        with tempfile.TemporaryDirectory() as temporary, patch.object(prepare.ex,'ROOT',Path(temporary)):
            receipt=Path(temporary)/'acceptance.json'
            receipt.write_text(json.dumps({'passed':False,'primary_post_reset_confirmed':True}))
            peer=Path(temporary)/'replica';peer.mkdir()
            (peer/'request.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError,'replica boundary is not reset'):
                rejected_boundaries(receipt)
            (peer/'request.json').unlink()
            receipt.write_text(json.dumps({'passed':False,'primary_post_reset_confirmed':False}))
            with self.assertRaisesRegex(RuntimeError,'primary boundary is not reset'):
                rejected_boundaries(receipt)
    def test_only_undeclared_external_nodeports_normalise(self):
        first={'type':'LoadBalancer','ports':[{'name':'http','port':8080,'targetPort':8080,'nodePort':32001}]}
        second=copy.deepcopy(first);second['ports'][0]['nodePort']=32002
        declared={'ports':[{'name':'http','port':8080,'targetPort':8080}]}
        self.assertEqual(normalise_service_ports(copy.deepcopy(first),declared),normalise_service_ports(copy.deepcopy(second),declared))
        pinned=copy.deepcopy(declared);pinned['ports'][0]['nodePort']=32001
        self.assertNotEqual(normalise_service_ports(copy.deepcopy(first),pinned),normalise_service_ports(copy.deepcopy(second),pinned))

    def test_service_target_port_and_selector_differences_remain(self):
        first={'ports':[{'name':'http','port':8080,'targetPort':8080,'nodePort':32001}],'selector':{'service':'web'}}
        second=copy.deepcopy(first);second['ports'][0]['targetPort']=9999
        declared={'ports':[{'name':'http','port':8080,'targetPort':8080}]}
        self.assertNotEqual(normalise_service_ports(copy.deepcopy(first),declared),normalise_service_ports(copy.deepcopy(second),declared))
        second=copy.deepcopy(first);second['selector']['service']='wrong'
        self.assertNotEqual(normalise_service_ports(copy.deepcopy(first),declared),normalise_service_ports(copy.deepcopy(second),declared))
    def test_validation_expiry_during_common_gate_does_not_allow_execution(self):
        snapshot,current=inputs();current['cart_check']=True
        now=[100];calls=[0]
        def collect(*_):
            calls[0]+=1
            if calls[0]>=4:now[0]=120
            value=copy.deepcopy(snapshot);value.update(started_at=now[0],completed_at=now[0])
            return value
        def validate(*args):
            return {'candidate_sha256':'fixed','contract_sha256':a.digest(current),
                'completed_at':100,'outcome':'Successful repair','fidelity_passed':True,
                'relevant_revision':'revision-A'}
        def gate(*_):now[0]=131;return {'passed':True}
        r=w.evaluate('P1','fixed',current,{},collect,gate,config=legacy.CONFIG,
            validate=validate,clock=lambda:now[0],monotonic=lambda:100)
        self.assertFalse(r['execution_permitted'])
        self.assertEqual(r['initial_decision'],'Revalidate')
        self.assertEqual(r['final_decision'],'Stop reuse')

    def test_validation_failure_is_reassessed_and_stops(self):
        snapshot,current=inputs();current['cart_check']=True
        validation={'candidate_sha256':'fixed','contract_sha256':a.digest(current),
            'completed_at':99,'outcome':'Confirmed failure','fidelity_passed':True,
            'relevant_revision':'revision-A'}
        r=w.evaluate('P1','fixed',current,{},lambda *_:snapshot,lambda *_:{'passed':True},
            config=legacy.CONFIG,validate=lambda *_:validation,clock=lambda:100,monotonic=lambda:100)
        self.assertFalse(r['execution_permitted'])
        self.assertEqual([x['decision'] for x in r['assessments']],['Revalidate','Revalidate','Stop reuse'])

    def test_expired_actual_outcome_cannot_be_restamped_as_cleanup_completion(self):
        snapshot,current=inputs();current['cart_check']=True
        validation={'candidate_sha256':'fixed','contract_sha256':a.digest(current),
            'completed_at':1,'cleanup_completed_at':100,'outcome':'Successful repair','fidelity_passed':True,
            'relevant_revision':'revision-A'}
        r=w.evaluate('P1','fixed',current,{},lambda *_:snapshot,lambda *_:{'passed':True},
            config=legacy.CONFIG,validate=lambda *_:validation,clock=lambda:100,monotonic=lambda:100)
        self.assertFalse(r['execution_permitted'])

    def test_overall_budget_includes_validation(self):
        snapshot,current=inputs();current['cart_check']=True;tick=[100]
        def validate(*_):tick[0]=1000;return None
        r=w.evaluate('P1','fixed',current,{},lambda *_:snapshot,lambda *_:{'passed':True},
            config=legacy.CONFIG,validate=validate,clock=lambda:tick[0],monotonic=lambda:tick[0])
        self.assertFalse(r['execution_permitted'])
        self.assertEqual(r['stop_reason'],'Overall decision budget exhausted')

    def test_actual_outcome_phase_audit_accepts_prior_positive_and_negative_traces(self):
        root=ROOT/'results/pre-main-development-v4'
        p=json.loads((root/'protocol.json').read_text())
        for name in ('DEV-P2-minimum1-repair-attempt-1','DEV-P2-minimum4-repair-attempt-1'):
            candidates=list(root.glob(name+'/record.json'))
            self.assertEqual(len(candidates),1)
            r=json.loads(candidates[0].read_text());r.pop('post_reset_confirmed');r.pop('completed_at')
            self.assertTrue(outcome_checks(r,p['timing'])['accepted'])
            # Reset confirmation is not required to certify the earlier phase.
            self.assertNotIn('post_reset_confirmed',r)
            bad=copy.deepcopy(r);bad['target_recovery']=not r['target_recovery']
            self.assertFalse(outcome_checks(bad,p['timing'])['accepted'])

    def test_incomplete_window_and_missing_necessary_action_cannot_publish(self):
        root=ROOT/'results/pre-main-development-v4';p=json.loads((root/'protocol.json').read_text())
        r=json.loads((root/'DEV-P2-minimum1-repair-attempt-1/record.json').read_text())
        bad=copy.deepcopy(r);bad['samples'][-1]['window_seconds']=10
        self.assertFalse(outcome_checks(bad,p['timing'])['accepted'])
        bad=copy.deepcopy(r);bad['execution']['task_events']=[]
        self.assertFalse(outcome_checks(bad,p['timing'])['accepted'])


if __name__=='__main__':unittest.main(verbosity=2)

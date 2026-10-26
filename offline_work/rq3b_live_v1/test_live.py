"""Adversarial scope/deadline/retention checks; fixtures are not experiment results."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
import os
from types import SimpleNamespace

from tools import rq3b_live as live
from .engine import Engine, Limits, Plan
from . import api
from offline_work.rq3b_api_v1.client import checked_candidate

ROOT=live.ROOT
spec=importlib.util.spec_from_file_location('original_controller_fixtures',ROOT/'offline_work/rq3b_v1/test_controller.py')
# The old test module uses an unqualified import. Bind it only while loading fixtures.
old=importlib.util.spec_from_file_location('controller',ROOT/'offline_work/rq3b_v1/controller.py')
module=importlib.util.module_from_spec(old);old.loader.exec_module(module)
with patch.dict('sys.modules',{'controller':module}):
    fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)

class Port(fixtures.Port):
    role='pilot'
    def registration_verified(self):return True
    def publish(self, record):self.last_record=copy.deepcopy(record)
    def plan(self, observation, remaining):
        self.step('plan',remaining);return Plan('generated',fixtures.HISTORICAL.content,'generated')

class LiveChecks(unittest.TestCase):
    def engine(self):
        clock=fixtures.Clock();port=Port(clock)
        e=Engine(port,clock,Limits(1000,30,1,1,1,6),Plan('P5','old bytes','historical'))
        return e,port
    def test_live_registration_is_required(self):
        e,p=self.engine();p.registration_verified=lambda:False
        with self.assertRaises(ValueError):Engine(p,e.clock,e.limits,e.historical)
    def test_long_replay_can_use_fresh_identical_revision(self):
        e,p=self.engine();p.delays['isolated_replay']=210
        r=e.run_sequence('full_revalidation','s','start',[{'id':'1'}])
        self.assertTrue(r['records'][0]['recovered'])
        self.assertEqual(r['records'][0]['generated_attempts'],0)
    def test_long_generation_can_use_fresh_identical_revision(self):
        e,p=self.engine();p.delays['plan']=60
        r=e.run_sequence('replanning','s','start',[{'id':'1'}])
        self.assertTrue(r['records'][0]['recovered'])
    def test_changed_revision_at_gate_blocks_launch(self):
        e,p=self.engine();p.mutate_at_gate=True
        r=e.run_sequence('replanning','s','start',[{'id':'1'}])
        self.assertFalse(r['records'][0]['recovered'])
        self.assertEqual(r['records'][0]['live_attempts'],[])
    def test_replay_failure_runs_generated_fallback_without_invalid_live_reuse(self):
        e,p=self.engine();p.replica_outcomes=['Confirmed failure']
        r=e.run_sequence('full_revalidation','s','start',[{'id':'1'}])['records'][0]
        self.assertTrue(r['recovered']);self.assertEqual(r['incidents_with_invalid_live_execution'],0)
        self.assertEqual(r['live_attempts'][0]['plan_origin'],'generated')
    def test_repairs_and_history_survive_second_incident(self):
        e,p=self.engine();r=e.run_sequence('proposed','s','start',[{'id':'1'},{'id':'2'}])
        self.assertEqual(p.state['data_writes'],2);self.assertEqual(len(r['history']),2)
        self.assertEqual(len(p.histories_seen[1]),1)
    def test_unresolved_incident_stops_and_retains_unpresented_count(self):
        e,p=self.engine();p.live_outcomes=['Confirmed failure']
        r=e.run_sequence('replanning','s','start',[{'id':'1'},{'id':'2'}])
        self.assertEqual(r['unpresented_incidents'],1);self.assertEqual(r['unresolved_incidents'],1)
    def test_no_fidelity_does_not_allow_historical_live_execution(self):
        e,p=self.engine();p.fidelity=False
        r=e.run_sequence('full_revalidation','s','start',[{'id':'1'}])['records'][0]
        self.assertEqual(r['live_attempts'][0]['plan_origin'],'generated')
    def test_historical_candidate_passes_shared_scope_gate(self):
        plan=Plan('P5',(ROOT/'candidates/P5.yaml').read_text(),'historical')
        self.assertEqual(live.check_plan(plan,'k3d-lab'),[{'service':'web'}])
    def test_generated_plan_cannot_delete_volumes_or_use_shell(self):
        value='- hosts: localhost\n  gather_facts: false\n  tasks:\n  - ansible.builtin.shell: kubectl delete pvc --all\n'
        with self.assertRaises(ValueError):live.check_plan(Plan('bad',value,'generated'),'k3d-lab')
    def test_generated_plan_cannot_change_ports(self):
        value=(ROOT/'candidates/P5.yaml').read_text().replace('"selector":{"service":"web"}', '"ports":[]')
        with self.assertRaises(ValueError):live.check_plan(Plan('bad',value,'generated'),'k3d-lab')
    def test_json_selector_patch_and_equal_flags_have_the_same_scope(self):
        value=[{'hosts':'localhost','gather_facts':False,'tasks':[{'ansible.builtin.command':{'argv':[
            'kubectl','--context=k3d-lab','--namespace=robot-shop','patch','service','web','--type=json','-p',
            json.dumps([{'op':'replace','path':'/spec/selector','value':{'service':'frontend'}}])]}}]}]
        self.assertEqual(len(live.check_plan(Plan('new',live.yaml.safe_dump(value),'generated'),'k3d-lab')),1)
    def test_patch_equals_and_reordered_options_share_the_literal_scope(self):
        patch_value=json.dumps({'spec':{'selector':{'service':'web','note':'a=b'}}})
        prefix=['kubectl','--context=k3d-lab','--namespace=robot-shop','patch','service','web']
        for options in [['--type=merge','--patch='+patch_value],['--patch='+patch_value,'--type','merge'],['-p='+patch_value,'--type=merge']]:
            value=[{'hosts':'localhost','gather_facts':False,'tasks':[{'ansible.builtin.command':{'argv':prefix+options}}]}]
            self.assertEqual(live.check_plan(Plan('new',live.yaml.safe_dump(value),'generated'),'k3d-lab'),[{'service':'web','note':'a=b'}])
    def test_duplicate_or_file_patch_options_remain_rejected(self):
        prefix=['kubectl','--context=k3d-lab','--namespace=robot-shop','patch','service','web']
        patch_value=json.dumps({'spec':{'selector':{'service':'web'}}})
        for options in [['--patch='+patch_value,'--patch='+patch_value],['--type=merge','--patch-file=/tmp/untrusted'],['--type=merge','--patch='+patch_value,'--type=json']]:
            value=[{'hosts':'localhost','gather_facts':False,'tasks':[{'ansible.builtin.command':{'argv':prefix+options}}]}]
            with self.assertRaises(ValueError):live.check_plan(Plan('bad',live.yaml.safe_dump(value),'generated'),'k3d-lab')
    def test_wrong_namespace_or_context_is_blocked(self):
        value=(ROOT/'candidates/P5.yaml').read_text().replace('- robot-shop','- default')
        with self.assertRaises(ValueError):live.check_plan(Plan('bad',value,'generated'),'k3d-lab')
    def test_external_token_control_does_not_claim_client_token_enforcement(self):
        response={'model':'gpt-6-sol','status':'completed','usage':{'output_tokens':3000},
            'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':json.dumps({'status':'candidate','plan_yaml':'example','rationale':'current'})}]}]}
        self.assertEqual(checked_candidate(response,'gpt-6-sol',None)['status'],'candidate')
        with self.assertRaises(ValueError):checked_candidate(response,'gpt-6-sol',2048)
    def test_model_call_is_stopped_at_total_deadline(self):
        class Child:
            pid=101;returncode=-15
            def wait(self,timeout=None):
                if timeout!=5:raise api.subprocess.TimeoutExpired('worker',timeout)
        with tempfile.TemporaryDirectory() as tmp,patch.object(api.subprocess,'Popen',return_value=Child()),patch.object(api.os,'killpg') as stop:
            with self.assertRaises(RuntimeError):api.plan(Path(tmp)/'attempt',{},0.01)
            receipt=json.loads((Path(tmp)/'attempt/bounded-call.json').read_text())
            self.assertTrue(receipt['deadline_reached']);self.assertFalse(receipt['accepted']);stop.assert_called_once()
    def test_three_recovered_processes_do_not_by_themselves_accept_live_adapters(self):
        results=[{'process':p,'records':[],'recovered_incidents':1} for p in ('proposed','replanning','full_revalidation')]
        with self.assertRaises(RuntimeError):live.accept_pilots(results,Path('/unused'))
    def test_reset_recreates_changed_immutable_selector_without_deleting_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manifest=root/'baseline.yaml'
            manifest.write_text(live.yaml.safe_dump({'kind':'Deployment','metadata':{'name':'web'},'spec':{'selector':{'matchLabels':{'service':'web'}}}}))
            changed={'spec':{'selector':{'matchLabels':{'app':'web'}}}}
            with patch.object(live,'BASE',manifest),patch.object(live.rb,'kjson',return_value=changed),patch.object(live.rb,'kubectl') as mutation,patch.object(live.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as reset:
                live.restore('k3d-lab',root/'reset.log')
            self.assertEqual(mutation.call_args_list[0].args,('scale','deployment/load','--replicas=0'))
            self.assertEqual(mutation.call_args_list[1].args,('delete','deployment/web','--cascade=foreground','--wait=true','--timeout=120s'))
            self.assertEqual(mutation.call_count,2);reset.assert_called_once()
            self.assertEqual(json.loads((root/'reset.selector-reset.json').read_text())['removed_workloads'],['Deployment/web'])
    def test_failed_immutable_workload_cleanup_blocks_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manifest=root/'baseline.yaml'
            manifest.write_text(live.yaml.safe_dump({'kind':'Deployment','metadata':{'name':'web'},'spec':{'selector':{'matchLabels':{'service':'web'}}}}))
            with patch.object(live,'BASE',manifest),patch.object(live.rb,'kjson',return_value={'spec':{'selector':{'matchLabels':{'app':'web'}}}}),patch.object(live.rb,'kubectl',side_effect=[None,RuntimeError('not deleted')]),patch.object(live.subprocess,'run') as reset:
                with self.assertRaises(RuntimeError):live.restore('k3d-lab',root/'reset.log')
                reset.assert_not_called()
    def test_unchanged_selector_does_not_recreate_workload(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manifest=root/'baseline.yaml';selector={'matchLabels':{'service':'web'}}
            manifest.write_text(live.yaml.safe_dump({'kind':'Deployment','metadata':{'name':'web'},'spec':{'selector':selector}}))
            with patch.object(live,'BASE',manifest),patch.object(live.rb,'kjson',return_value={'spec':{'selector':selector}}),patch.object(live.rb,'kubectl') as mutation,patch.object(live.subprocess,'run',return_value=SimpleNamespace(returncode=0)):
                live.restore('k3d-lab',root/'reset.log')
            mutation.assert_not_called()
    def test_prior_pilot_with_changed_settings_cannot_be_carried(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();source=root/'results/prior';source.mkdir(parents=True)
            (source/'protocol.json').write_text(json.dumps({'response_cap_seconds':1200}))
            protocol={'response_cap_seconds':900,'pilot_resume':{'source':'results/prior'}}
            with patch.object(live,'ROOT',root):
                with self.assertRaisesRegex(ValueError,'settings changed'):live.load_pilot_resume(protocol)
    def test_runner_artifacts_do_not_copy_model_credentials(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'DDN_LLM_API_KEY':'fake-model-secret','OPENAI_API_KEY':'fake-other-secret'}):
            root=Path(temp)
            plan=Plan('fixture','- hosts: localhost\n  gather_facts: false\n  tasks:\n  - ansible.builtin.command:\n      argv: [echo, fixture]\n','generated')
            port=live.Port(root,{},'unchanged','pilot')
            # Bypass only the Kubernetes mutation gate for a harmless local command.
            # Exercise the production private-directory creation and runner adapter.
            with patch.object(live,'check_plan',return_value=[]):
                execution=port.run_action(plan,root,'k3d-lab',30)
            self.assertTrue(execution['complete']);self.assertTrue(execution['runner_termination_confirmed'])
            command=next((root/'runner/artifacts').glob('*/command')).read_text()
            self.assertNotIn('fake-model-secret',command);self.assertNotIn('fake-other-secret',command)

if __name__=='__main__':unittest.main()

"""Boundary checks necessary for the prospective queue and policy inputs."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
import comparison_policies as policies
import formal_collection_v2 as collection
import formal_methods as methods
import prepare_formal_main as prepare
import formal_replay_executor_v2 as executor

class FormalChecks(unittest.TestCase):
    def test_exact_design_counts_and_deterministic_order(self):
        p=json.loads((collection.ROOT/'results/pre-main-development-v4/protocol.json').read_text())
        controls=[x for x in p['controls'] if not x['id'].startswith('DEV-')]
        unused=[{**x,'id':x['candidate']+'-unrelated-change','unrelated_control':'unused'} for x in controls]
        order=prepare.make_order(p['scenarios'],controls+unused,20261004)
        self.assertEqual(order,prepare.make_order(p['scenarios'],controls+unused,20261004))
        self.assertEqual(len(order),295)
        self.assertEqual(len({x['trial_id'] for x in order}),295)
        for row in p['scenarios']:
            repeats=[x['repeat'] for x in order if x['scenario']==row['id'] and x['category']=='main']
            self.assertEqual(sorted(repeats),[1,2,3,4,5])
            self.assertEqual(sum(x['scenario']==row['id'] and x['arm']=='no-repair' for x in order),1)
    def test_fault_matching_cannot_fit_main_or_expected_labels(self):
        for field,value in [('main_trial',True),('label_source','expected_effect')]:
            pilots=[{'candidate':'P1','incident':{'workload':['web']},'outcome':outcome,
                     'label_source':'observed_development_replay','main_trial':False}
                    for outcome in ('Successful repair','Confirmed failure')]
            pilots[0][field]=value
            with self.assertRaises(ValueError):policies.fit_fault_matching({'P1':[{'workload':['web']}]},pilots)
    def test_whole_environment_has_no_collector_metadata(self):
        value={k:[] for k in methods.SCOPE}
        value.update(started_at=1,relevant_revision='old',p4_consumption_provenance={'private':'unused'})
        self.assertEqual(set(methods.scope_snapshot(value)),set(methods.SCOPE))
        a=methods.scope_snapshot(value);b=copy.deepcopy(a)
        self.assertEqual(policies.whole_environment(a,b,{}, {})['decision'],'Direct reuse')
        b['configmaps']=[{'metadata':{'name':'unused'},'data':{'UNUSED':'1'}}]
        self.assertEqual(policies.whole_environment(a,b,{}, {})['decision'],'Revalidate')
    def test_verification_provenance_not_mistaken_for_criteria(self):
        p=json.loads((collection.ROOT/'results/pre-main-development-v4/protocol.json').read_text())
        p['verifier_sha256']='fixed';row=p['scenarios'][0]
        a=methods.criteria(row,p);p['protocol_sha256']='new source record'
        self.assertEqual(a,methods.criteria(row,p))
        row=copy.deepcopy(row);row['construction']['cart_check']=True
        self.assertNotEqual(a,methods.criteria(row,p))
    def test_no_expected_effect_present_in_evaluator_scenarios(self):
        p=json.loads((collection.ROOT/'results/pre-main-development-v4/protocol.json').read_text())
        self.assertTrue(all('expected_effect' not in r and r.get('outcome_label') is None for r in p['scenarios']))
    def test_freeze_rejects_modified_protocol(self):
        path=collection.ROOT/'results/comparison-calibration-v3'
        self.assertEqual(collection.check(path)['main_trials'],0)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);(out/'protocol.json').write_bytes((path/'protocol.json').read_bytes())
            (out/'freeze.json').write_bytes((path/'freeze.json').read_bytes())
            p=json.loads((out/'protocol.json').read_text());p['timing']['outcome_window']=1
            collection.save(out/'protocol.json',p)
            with self.assertRaises(AssertionError):collection.check(out)

if __name__=='__main__':unittest.main()

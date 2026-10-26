import copy
import unittest
import comparison_policies as p


class ComparisonTests(unittest.TestCase):
    def test_outcome_and_injection_metadata_cannot_enter_incident_text(self):
        a={'workload':'web','symptoms':['HTTP 503'],'error_codes':['7']}
        b={**a,'outcome':'success','fault_label':'pod-failure','expected_effect':'invalid','scenario':'P1-1-2'}
        self.assertEqual(p.incident_text(a),p.incident_text(b))

    def test_normalise_generated_ids_not_http_codes(self):
        words=p.incident_text({'workload':'web-8467957bfd-4kljt','symptoms':['2026-10-03T12:00:00Z HTTP 503']})
        self.assertEqual(words,['web','http','503'])

    def test_positive_only_cannot_freeze_threshold(self):
        positive={'candidate':'P1','incident':{'workload':'web'},'outcome':'Successful repair','label_source':'observed_development_replay'}
        with self.assertRaises(ValueError):p.fit_fault_matching({'P1':[positive['incident']]},[positive])

    def test_main_outcomes_and_expected_labels_rejected(self):
        pilots=[{'candidate':'P1','incident':{'workload':'web'},'outcome':label,'label_source':'observed_development_replay'}
                for label in ('Successful repair','Confirmed failure')]
        pilots[0]['main_trial']=True
        with self.assertRaises(ValueError):p.fit_fault_matching({'P1':[{'workload':'web'}]},pilots)
        pilots[0]['main_trial']=False;pilots[0]['label_source']='expected_effect'
        with self.assertRaises(ValueError):p.fit_fault_matching({'P1':[{'workload':'web'}]},pilots)

    def test_fit_and_fixed_vocabulary(self):
        pilots=[{'candidate':'P1','incident':{'workload':name},'outcome':outcome,'label_source':'observed_development_replay'}
                for name,outcome in [('web','Successful repair'),('other','Confirmed failure')]]
        model=p.fit_fault_matching({'P1':[{'workload':'web'}]},pilots);before=copy.deepcopy(model['idf'])
        self.assertEqual(model['pilot_balanced_accuracy'],1.0)
        self.assertEqual(p.fault_matching(model,'P1',{'workload':'web'})['decision'],'Direct reuse')
        p.fault_matching(model,'P1',{'workload':'never-seen-before'})
        self.assertEqual(model['idf'],before)

    def test_broad_comparator_ignores_volatile_metadata_but_keeps_specs(self):
        old={'metadata':{'name':'web','uid':'old','resourceVersion':'1'},'spec':{'replicas':1}}
        new=copy.deepcopy(old);new['metadata'].update(uid='new',resourceVersion='2')
        self.assertEqual(p.whole_environment(old,new,{'v':1},{'v':1})['decision'],'Direct reuse')
        new['spec']['replicas']=2
        self.assertEqual(p.whole_environment(old,new,{'v':1},{'v':1})['decision'],'Revalidate')
        self.assertEqual(p.whole_environment(old,old,{'v':1},{'v':2})['decision'],'Revalidate')


if __name__=='__main__':unittest.main(verbosity=2)

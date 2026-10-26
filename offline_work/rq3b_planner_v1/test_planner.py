import json
import unittest
from planner import validate_output, MAX_FINAL_BYTES

class Checks(unittest.TestCase):
    def candidate(self):return json.dumps({'status':'candidate','plan_yaml':'- hosts: localhost','rationale':'Current evidence'}).encode()
    def test_completed_candidate(self):
        self.assertEqual(validate_output(self.candidate(),[{'type':'turn.completed'}])['status'],'candidate')
    def test_tool_call_is_not_admitted(self):
        with self.assertRaises(ValueError):validate_output(self.candidate(),[{'type':'item.completed','item':{'type':'command_execution'}},{'type':'turn.completed'}])
    def test_no_model_completion_is_not_success(self):
        with self.assertRaises(ValueError):validate_output(self.candidate(),[])
    def test_extra_fields_rejected(self):
        x=json.loads(self.candidate());x['recovery']=True
        with self.assertRaises(ValueError):validate_output(json.dumps(x).encode(),[{'type':'turn.completed'}])
    def test_unresolved_cannot_hide_a_plan(self):
        x=json.loads(self.candidate());x['status']='unresolved'
        with self.assertRaises(ValueError):validate_output(json.dumps(x).encode(),[{'type':'turn.completed'}])
    def test_size_limit_is_admission_only(self):
        with self.assertRaises(ValueError):validate_output(b' '*(MAX_FINAL_BYTES+1),[{'type':'turn.completed'}])
    def test_empty_rationale(self):
        x=json.loads(self.candidate());x['rationale']=''
        with self.assertRaises(ValueError):validate_output(json.dumps(x).encode(),[{'type':'turn.completed'}])
    def test_unresolved_evidence_retained(self):
        x={'status':'unresolved','plan_yaml':'','rationale':'Insufficient current evidence'}
        self.assertEqual(validate_output(json.dumps(x).encode(),[{'type':'turn.completed'}])['status'],'unresolved')

if __name__=='__main__':unittest.main()

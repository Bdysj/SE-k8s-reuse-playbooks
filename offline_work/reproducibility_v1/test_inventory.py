import copy
import json
from pathlib import Path
import tempfile
import unittest

from build_inventory import (COUNTS, FAMILIES, METHODS, Index, named_blocks,
                             scalar, section, validate_design, validate_response_queue,
                             validate_qualification)

def fixture():
    candidates = [{'id':f'P{i}', 'registered':True} for i in range(1,6)]
    scenarios = [{'id':f'{c["id"]}-{f}-{n}', 'candidate':c['id'], 'condition':f,
                  'expectation_is_not_outcome_label':True}
                 for c in candidates for f in FAMILIES for n in (1,2)]
    rows = []
    for s in scenarios:
        for repeat in range(1,6):
            rows.append({'trial_id':s['id']+f'-r{repeat}','scenario':s['id'], 'candidate':s['candidate'],
                         'condition':s['condition'],'repeat':repeat,'category':'main','arm':'repair'})
    for category, count in COUNTS.items():
        if category != 'main':
            rows.extend({'trial_id':f'{category}-{i}','category':category} for i in range(count))
    queue = []
    for s in scenarios:
        for repeat in (1,2):
            for method in METHODS:
                position = len(queue)+1
                queue.append({'position':position,'scenario':s['id'],'candidate':s['candidate'],
                              'repeat':repeat,'method':method,'trial_id':f'{s["id"]}-r{repeat}-{method}',
                              'name':f'{position:03d}-{s["id"]}-r{repeat}-{method}'})
    return candidates,scenarios,rows,queue

class DesignTests(unittest.TestCase):
    def setUp(self):
        self.c,self.s,self.r,self.q = fixture()

    def test_complete_fixed_design(self):
        validate_design(self.c,self.s,self.r)
        validate_response_queue(self.q,self.s)

    def test_duplicate_candidate_rejected(self):
        self.c[-1]['id'] = 'P1'
        with self.assertRaises(ValueError): validate_design(self.c,self.s,self.r)

    def test_duplicate_scenario_rejected(self):
        self.s[-1]['id'] = self.s[0]['id']
        with self.assertRaises(ValueError): validate_design(self.c,self.s,self.r)

    def test_missing_condition_cell_rejected(self):
        self.s[-1]['condition'] = FAMILIES[0]
        with self.assertRaises(ValueError): validate_design(self.c,self.s,self.r)

    def test_hypothesis_outcome_substitution_rejected(self):
        self.s[0]['expectation_is_not_outcome_label'] = False
        with self.assertRaises(ValueError): validate_design(self.c,self.s,self.r)

    def test_missing_main_repeat_rejected(self):
        self.r[0]['repeat'] = 2
        with self.assertRaises(ValueError): validate_design(self.c,self.s,self.r)

    def test_raw_scenario_candidate_mismatch_rejected(self):
        self.r[0]['candidate'] = 'P5'
        with self.assertRaises(ValueError): validate_design(self.c,self.s,self.r)

    def test_control_count_reassignment_rejected(self):
        self.r[-1]['category'] = 'main'
        with self.assertRaises(ValueError): validate_design(self.c,self.s,self.r)

    def test_response_missing_repeat_rejected(self):
        self.q[3]['repeat'] = 1
        with self.assertRaises(ValueError): validate_response_queue(self.q,self.s)

    def test_response_method_substitution_rejected(self):
        self.q[0]['method'] = 'unregistered_method'
        with self.assertRaises(ValueError): validate_response_queue(self.q,self.s)

    def test_response_duplicate_directory_rejected(self):
        self.q[-1]['name'] = self.q[0]['name']
        with self.assertRaises(ValueError): validate_response_queue(self.q,self.s)

    def test_response_order_drift_rejected(self):
        self.q[0]['position'] = 2
        with self.assertRaises(ValueError): validate_response_queue(self.q,self.s)

class IntegrityTests(unittest.TestCase):
    def test_qualification_uses_saved_physical_flags(self):
        raw = {'candidate':'P1','repeat':3,'protocol_sha256':'abc', 'passed':True,
               'outcome_passed':True,'execution_complete':True,'target_recovery':True,
               'non_target_regression':False,'post_reset_confirmed':True}
        record = {'repeat':3,'protocol_sha256':'abc'}
        validate_qualification(raw,record,'P1')
        for field,value in [('execution_complete',False),('target_recovery',False),
                            ('non_target_regression',True),('post_reset_confirmed',False)]:
            changed = dict(raw); changed[field] = value
            with self.assertRaises(ValueError): validate_qualification(changed,record,'P1')

    def test_qualification_identity_and_protocol_drift_rejected(self):
        raw = {'candidate':'P1','repeat':3,'protocol_sha256':'abc', 'passed':True,
               'outcome_passed':True,'execution_complete':True,'target_recovery':True,
               'non_target_regression':False,'post_reset_confirmed':True}
        with self.assertRaises(ValueError): validate_qualification(raw,{'repeat':4,'protocol_sha256':'abc'},'P1')
        with self.assertRaises(ValueError): validate_qualification(raw,{'repeat':3,'protocol_sha256':'def'},'P1')

    def test_mismatch_missing_outside_and_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p = root/'record.json'; p.write_text('{"original":true}')
            idx = Index(root)
            before = p.read_bytes()
            ref = idx.reference('record.json')
            self.assertEqual(idx.read('record.json',ref['sha256']), {'original':True})
            self.assertEqual(before,p.read_bytes())
            with self.assertRaises(ValueError): idx.reference('record.json','0'*64)
            with self.assertRaises(ValueError): idx.reference('missing.json')
            with self.assertRaises(ValueError): idx.reference('../outside.json')
            p.write_text('{"changed":true}')
            with self.assertRaises(ValueError): idx.verify_stability()

    def test_scalar_parser_rejects_ambiguous_pin(self):
        block = '  commit: a\n  commit: b\n'
        with self.assertRaises(ValueError): scalar(block,'commit',2)

    def test_section_parser_rejects_duplicate_section(self):
        with self.assertRaises(ValueError): section('pins:\n  a: b\npins:\n  a: c\n','pins')

    def test_narrow_parser_preserves_boundary_and_quoted_value(self):
        value = 'pins:\n  first:\n    ref: "one"\n  second:\n    ref: two\nnext:\n  ref: outside\n'
        blocks = named_blocks(section(value,'pins'),2)
        self.assertEqual(set(blocks),{'first','second'})
        self.assertEqual(scalar(blocks['first'],'ref',4),'one')
        self.assertEqual(scalar(blocks['second'],'ref',4),'two')

if __name__ == '__main__':
    unittest.main()

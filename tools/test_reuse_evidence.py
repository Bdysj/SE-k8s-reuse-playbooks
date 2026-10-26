import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

import reuse_evidence as e
from test_main_verifier import fixture


class EvidenceTests(unittest.TestCase):
    def test_relevant_revisions_exclude_unrelated_objects_but_detect_replacement(self):
        s=fixture();current={'serving_deployment':'web','frontend_service':'web','catalogue_service':'catalogue',
                            'cart_check':False,'vote_check':False}
        before=e.relevant_revision(s,'P1',current)
        unrelated=copy.deepcopy(s['deployments'][0]);unrelated['metadata'].update(name='unrelated',uid='different')
        s['deployments'].append(unrelated)
        self.assertEqual(before,e.relevant_revision(s,'P1',current))
        s['deployments'][0]['metadata']['uid']='replacement'
        self.assertNotEqual(before,e.relevant_revision(s,'P1',current))

    def test_safety_scope_and_code_hash_are_shared_execution_constraints(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'candidate';path.write_text('frozen candidate')
            h=hashlib.sha256(path.read_bytes()).hexdigest();s=fixture();s['p1_discovery_names']=['web']
            gate=lambda context='k3d-lab':e.common_safety_gate('P1',path,h,context,'robot-shop',s,['web'])
            self.assertTrue(gate()['passed'])
            self.assertFalse(gate('production')['passed'])
            s['p1_discovery_names']=['undeclared-web'];self.assertFalse(gate()['passed'])
            s['p1_discovery_names']=['web'];path.write_text('edited candidate');self.assertFalse(gate()['passed'])


if __name__=='__main__':unittest.main(verbosity=2)

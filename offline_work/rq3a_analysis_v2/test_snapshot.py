"""Input stability and incomplete-final guards use temporary fixture files."""
import json
from pathlib import Path
import tempfile
import unittest

from analysis import Reader, collect_snapshot, sha_bytes

class Checks(unittest.TestCase):
    def test_changed_snapshot_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'saved.json';path.write_text('{"value":1}')
            reader=Reader(root);reader.read(path);path.write_text('{"value":2}')
            with self.assertRaises(ValueError):reader.verify_stability()
    def test_outside_project_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);sub=root/'project';sub.mkdir();path=root/'outside.json';path.write_text('{}')
            with self.assertRaises(ValueError):Reader(sub).read(path)
    def test_incomplete_final_does_not_read_or_create_final_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cohort=root/'cohort';cohort.mkdir();(root/'candidates').mkdir()
            asset=root/'candidates/registration.v1.json';asset.write_text('{}');lock=root/'lock';lock.write_text('{}')
            protocol={'response_registered':True,'queue':[{}]*240,'candidate_hashes':{},'registration_sha256':sha_bytes(asset.read_bytes()),
                      'inputs':{},'source_lock':{'path':'lock','sha256':sha_bytes(lock.read_bytes())}}
            (cohort/'protocol.json').write_text(json.dumps(protocol))
            (cohort/'freeze.json').write_text(json.dumps({'protocol_sha256':sha_bytes((cohort/'protocol.json').read_bytes()),'source_hashes':{}}))
            (cohort/'progress.json').write_text(json.dumps({'state':'running','responses_completed':100}))
            with self.assertRaisesRegex(ValueError,'incomplete collection'):
                collect_snapshot(root,cohort,final=True)
            self.assertFalse((cohort/'collection-acceptance.json').exists())

if __name__ == '__main__':unittest.main()

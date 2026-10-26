"""Acceptance must cover actual proposed revalidation and safe uncertain decline."""
import unittest

from rq3a_registered_incident_v2 import validation_acceptance


class ResponseEvidenceTests(unittest.TestCase):
    def workflow(self, outcome='Successful repair', permitted=True):
        return {'replays': 1, 'execution_permitted': permitted,
                'validation_certificate': {'outcome': outcome}}

    def test_verified_actual_replay_counts_for_proposed_too(self):
        self.assertTrue(validation_acceptance(self.workflow(), {'passed': True}, lambda cert: True))

    def test_unverified_certificate_cannot_accept_live_execution(self):
        self.assertFalse(validation_acceptance(self.workflow(), {'passed': True}, lambda cert: False))

    def test_indeterminate_certificate_cannot_accept_live_execution(self):
        self.assertFalse(validation_acceptance(self.workflow('Indeterminate'),
                                              {'passed': True}, lambda cert: True))

    def test_uncertain_decline_requires_separately_confirmed_cleanup(self):
        workflow = self.workflow('Indeterminate', permitted=False)
        self.assertFalse(validation_acceptance(workflow, {'passed': False}, lambda cert: True))
        self.assertTrue(validation_acceptance(workflow, {'passed': True}, lambda cert: True))

    def test_direct_route_does_not_fabricate_a_validation_requirement(self):
        self.assertTrue(validation_acceptance({'replays': 0, 'execution_permitted': True},
            {'passed': True}, lambda cert: self.fail('No replay certificate exists')))


if __name__ == '__main__':
    unittest.main(verbosity=2)

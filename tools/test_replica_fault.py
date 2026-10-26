import unittest

import replica_fault as f


class ReplicaFaultTests(unittest.TestCase):
    def test_same_operator_faults_original_and_changed_conditions(self):
        self.assertEqual(f.desired_after_fault(1),0)
        self.assertEqual(f.desired_after_fault(3),1)
        # Both conditions are faulty against their declared original/changed
        # minima, while the candidate's unchanged arithmetic can restore them.
        self.assertLess(f.desired_after_fault(1),1)
        self.assertLess(f.desired_after_fault(3),2)
        self.assertGreaterEqual(f.desired_after_fault(1)+1,1)
        self.assertGreaterEqual(f.desired_after_fault(3)+1,2)

    def test_stricter_condition_is_not_automatically_rescued(self):
        self.assertLess(f.desired_after_fault(3)+1,3)

    def test_cannot_change_fault_strength_or_inject_already_failed_setup(self):
        for count,decrement in [(0,2),(-1,2),(True,2),(3,1),(3,3)]:
            with self.assertRaises(ValueError):f.desired_after_fault(count,decrement)


if __name__=='__main__':unittest.main(verbosity=2)

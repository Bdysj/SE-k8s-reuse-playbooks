"""Regressions from actually observed API quantities and conservative Unknowns."""
import unittest
from unittest.mock import patch

import resource_quantities as quantities
import supported_reuse_v2 as supported


class Mapping(unittest.TestCase):
    def snapshot(self,ready=True,taints=None):
        return {'scheduler_nodes':[{'name':'node','labels':{'beta.kubernetes.io/os':'linux'},
                'ready':ready,'pressure':False,'taints':taints or [],
                'allocatable':{'cpu':'8','memory':'8124776Ki'}}], 'scheduled_pods':[]}
    def deployment(self):return {'spec':{'template':{'spec':{'containers':[{'name':'web'}]}}}}
    def test_actual_live_node_quantity_supports_headroom(self):
        self.assertEqual(quantities.memory_bytes('8124776Ki'),8124776*1024)
        self.assertIs(supported.scheduler_mapping(self.snapshot(),self.deployment())[0],True)
    def test_case_sensitive_and_unmapped_forms_stay_unknown(self):
        for value in ('8124776ki','1.2Mi','1e9','garbage',True,None,'-1'):
            self.assertIsNone(quantities.memory_bytes(value))
        self.assertEqual(quantities.memory_bytes('256Mi'),quantities.memory_bytes('262144Ki'))
    def test_unknown_ready_does_not_become_contradiction(self):
        self.assertIsNone(supported.scheduler_mapping(self.snapshot(ready=None),self.deployment())[0])
    def test_taints_are_unsupported_not_false_label_proof(self):
        s=self.snapshot(taints=[{'key':'dedicated','effect':'PreferNoSchedule'}])
        self.assertIsNone(supported.scheduler_mapping(s,self.deployment())[0])
    def test_missing_actual_linux_label_is_contradiction(self):
        s=self.snapshot();s['scheduler_nodes'][0]['labels']={}
        self.assertIs(supported.scheduler_mapping(s,self.deployment())[0],False)
    def test_unresolved_dsn_reference_is_unknown_and_not_guessed(self):
        d={'metadata':{'name':'ratings'},'spec':{'template':{'spec':{'containers':[
            {'name':'ratings','env':[{'name':'PDO_URL','valueFrom':{'secretKeyRef':
                {'name':'unread-reference','key':'url'}}}]}]}}}}
        s={'deployments':[d],'relevant_revision':'test-revision'}
        with patch.object(supported.e,'collect',return_value=s), \
             patch.object(supported,'registered_container_unchanged',return_value=True), \
             patch.object(supported.v,'endpoint_binding',return_value={'healthy':True}), \
             patch.object(supported.rb,'kubectl') as call:
            observed=supported.collect('P2',{})
        self.assertIsNone(observed['p2_backend']['dsn_matches_registered_mysql'])
        self.assertIsNone(observed['p2_backend']['query_available'])
        call.assert_not_called()


if __name__=='__main__':unittest.main()

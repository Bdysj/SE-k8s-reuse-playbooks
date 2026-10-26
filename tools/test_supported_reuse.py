"""Supported semantic mappings; synthetic data does not label main outcomes."""
import copy
import unittest

import reuse_assessor as a
import supported_reuse as s
from test_reuse_assessor import inputs


def ratings_inputs():
    snapshot, current = inputs()
    d=copy.deepcopy(snapshot['deployments'][0]);d['metadata']['name']='ratings'
    d['spec']['template']['spec']['containers'][0]['name']='ratings'
    d['spec']['template']['metadata']['labels']={'service':'ratings'}
    snapshot['deployments'].append(d)
    service=copy.deepcopy(snapshot['services'][0]);service['metadata']['name']='ratings'
    service['spec']['selector']={'service':'ratings'};snapshot['services'].append(service)
    current.update(serving_deployment='ratings',target_container='ratings',
        target_path='/api/ratings/api/fetch/Watson',
        equivalent_endpoints={'P2':['/api/ratings/api/fetch/Watson']})
    return snapshot,current


def scheduling_inputs():
    snapshot,current=inputs();current['target_container']='web'
    current.update(target_path='/api/catalogue/products',equivalent_endpoints={'P3':['/api/catalogue/products']})
    snapshot['p3_fixed_affinity_supported']=True
    snapshot['scheduler_nodes']=[{'name':'node','ready':True,'unschedulable':False,'taints':[],
        'labels':{'beta.kubernetes.io/os':'linux'},'allocatable':{'cpu':'2','memory':'2Gi'},'pressure':False}]
    snapshot['scheduled_pods']=[]
    return snapshot,current


class SupportedTests(unittest.TestCase):
    def assess(self,candidate,snapshot,current,validation=None):
        rows=s.compare(candidate,'fixed',snapshot,current,now=100,validation=validation)
        return {r['condition']:r['state'] for r in rows}

    def test_frontend_selector_alone_does_not_prove_database_access(self):
        snapshot,current=ratings_inputs()
        self.assertEqual(self.assess('P2',snapshot,current)['resource_relationships'],a.UNKNOWN)

    def test_exact_database_account_and_consumption_mapping_suffices(self):
        snapshot,current=ratings_inputs()
        snapshot['p2_backend']={'registered_consumption_unchanged':True,
            'dsn_matches_registered_mysql':True,'service_binding':{'healthy':True},'query_available':True}
        self.assertEqual(self.assess('P2',snapshot,current)['resource_relationships'],a.SAT)
        snapshot['p2_backend']['query_available']=None
        self.assertEqual(self.assess('P2',snapshot,current)['resource_relationships'],a.UNKNOWN)

    def test_explicit_incompatible_database_route_is_contradiction(self):
        snapshot,current=ratings_inputs();snapshot['p2_backend']={'dsn_matches_registered_mysql':False}
        self.assertEqual(self.assess('P2',snapshot,current)['resource_relationships'],a.CONTRA)

    def test_target_container_cannot_be_inferred_from_deployment_name(self):
        snapshot,current=ratings_inputs()
        snapshot['deployments'][-1]['spec']['template']['spec']['containers'][0]['name']='unmapped'
        self.assertEqual(self.assess('P2',snapshot,current)['target_objects'],a.CONTRA)

    def test_linux_label_alone_cannot_prove_schedulability(self):
        snapshot,current=scheduling_inputs();snapshot.pop('scheduled_pods')
        self.assertEqual(self.assess('P3',snapshot,current)['referenced_fields'],a.UNKNOWN)

    def test_supported_replacement_fits_observed_request_capacity(self):
        snapshot,current=scheduling_inputs()
        self.assertEqual(self.assess('P3',snapshot,current)['referenced_fields'],a.SAT)

    def test_conservative_capacity_accounts_for_other_and_init_requests(self):
        snapshot,current=scheduling_inputs()
        snapshot['scheduled_pods']=[{'nodeName':'node','phase':'Running',
            'containers':[{'resources':{'requests':{'cpu':'10m','memory':'10Mi'}}}],
            'initContainers':[{'resources':{'requests':{'cpu':'1901m','memory':'1900Mi'}}}]}]
        self.assertEqual(self.assess('P3',snapshot,current)['referenced_fields'],a.UNKNOWN)

    def test_no_matching_ready_node_is_confirmed_placement_contradiction(self):
        snapshot,current=scheduling_inputs();snapshot['scheduler_nodes'][0]['ready']=False
        self.assertEqual(self.assess('P3',snapshot,current)['referenced_fields'],a.CONTRA)

    def test_hard_anti_affinity_or_missing_pressure_proof_remains_unknown(self):
        snapshot,current=scheduling_inputs()
        snapshot['deployments'][0]['spec']['template']['spec']['affinity']={
            'podAntiAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':[{'topologyKey':'hostname'}]}}
        self.assertEqual(self.assess('P3',snapshot,current)['referenced_fields'],a.UNKNOWN)
        snapshot,current=scheduling_inputs();snapshot['scheduler_nodes'][0]['pressure']=None
        self.assertEqual(self.assess('P3',snapshot,current)['referenced_fields'],a.UNKNOWN)

    def test_supplemental_uncertainty_needs_explicit_validated_requirement(self):
        snapshot,current=ratings_inputs()
        validation={'candidate_sha256':'fixed','contract_sha256':a.digest(current),
            'fidelity_passed':True,'outcome':'Successful repair','completed_at':99,
            'relevant_revision':'revision-A','verified_requirements':[]}
        self.assertEqual(self.assess('P2',snapshot,current,validation)['resource_relationships'],a.UNKNOWN)
        validation['verified_requirements']=['resource_relationships']
        self.assertEqual(self.assess('P2',snapshot,current,validation)['resource_relationships'],a.SAT)
        validation['relevant_revision']=None;snapshot.pop('relevant_revision')
        self.assertEqual(self.assess('P2',snapshot,current,validation)['resource_relationships'],a.UNKNOWN)


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Fixed initial RQ3a decisions; independent of later counterfactual outcomes."""
import json
import time
import main_verifier as v
import reset_baseline as rb
import reuse_assessor as assessor
import supported_reuse_v2 as supported
import comparison_policies as policies
import incident_observations as incidents

SCOPE = ('deployments','statefulsets','replicasets','pods','services',
         'endpointslices','configmaps','horizontalpodautoscalers')

def namespace_snapshot():
    start=time.time()
    data={kind:rb.kjson('get',kind)['items'] for kind in SCOPE}
    return {'started_at':start,'completed_at':time.time(),**data}

def scope_snapshot(observation):
    # Neither collector annotations nor future outcomes enter broad comparison.
    return {kind:observation[kind] for kind in SCOPE}

def criteria(row,p):
    fields=('target_deployment','target_container','frontend_service','catalogue_service',
            'required_replicas','target_path','target_pattern','cart_check','vote_check',
            'endpoint_owner_check','preserve_cart_host')
    return {'version':'current-owner-verifier-v1','target':{k:row['construction'].get(k) for k in fields},
            'non_target_workloads':row['non_target_workloads'],'timing':p['timing'],
            'predicate_source_sha256':p['verifier_sha256']}

def decisions(p,row,observed,current,first_sample):
    states=supported.compare(row['candidate'],p['candidate_hashes'][row['candidate']],
                             observed,current,ttl=p['decision_configuration']['observation_ttl'])
    incident=incidents.observed_incident(first_sample,row['construction']['target_deployment'])
    result={'at':time.time(),'incident':incident,'proposed':{'states':states,**assessor.decision(states)},
            'input_boundary':'before any candidate action; no intended change, injection label or outcome',
            'ablations':{}}
    for omitted in ('target_objects','referenced_fields','resource_relationships','verification_criteria'):
        result['ablations'][omitted]=assessor.decision([s for s in states if s['condition']!=omitted])
    if p.get('comparison_model'):
        model=json.loads((rb.ROOT/p['comparison_model']).read_text())['model']
        result['fault_matching']=policies.fault_matching(model,row['candidate'],incident)
        ref=json.loads((rb.ROOT/p['whole_environment_references']).read_text())[row['candidate']]
        result['whole_environment']=policies.whole_environment(ref['snapshot'],scope_snapshot(observed),
                                                               ref['criteria'],criteria(row,p))
        result['scope']='Initial decisions and counterfactual RQ1 replay only. Actual subsequent RQ3a routes are separate trials.'
    else:
        result['scope']='Development snapshot collection; comparison threshold not yet fitted.'
    return result

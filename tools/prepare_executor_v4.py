"""Restore the planned P2 design contrast using one same-parameter fault operator."""
import copy
import json
import time

import yaml
import main_executor_v3 as previous
import main_executor_v4 as executor
import replica_fault


def prepare():
    root=executor.ROOT;parent=root/'results/pre-main-development-v3'
    p=copy.deepcopy(previous.inputs(parent));out=root/'results/pre-main-development-v4'
    out.mkdir(exist_ok=False)
    p.update(version='pre-main-development-v4',written_at=time.time(),
        predecessor_protocol=str((parent/'protocol.json').relative_to(root)),
        predecessor_sha256=executor.sha(parent/'protocol.json'),
        source_version_reason='Absolute replicas=0 makes both minima2/3 examples invalidating. '
            'The document predeclares a potentially preserving example. Before any main trial, '
            'use the identical decrement-two/clamp-zero fault operator for every P2 case and '
            'its original-condition control. Healthy replicas3 with minimum2/3 yield the '
            'prospective arithmetic contrast without changing a frozen candidate or its input.')
    for row in p['scenarios']+p['controls']:
        if row['candidate']!='P2':continue
        documents=list(yaml.safe_load_all((root/row['healthy_manifest']).read_text()))
        d=next(d for d in documents if d and d['kind']=='Deployment'
               and d['metadata']['name']==row['construction']['target_deployment'])
        if row['id'] in ('P2-2-1','P2-2-2'):
            d['spec']['replicas']=3
            path=out/(row['id']+'.healthy.yaml')
            path.write_text(yaml.safe_dump_all(documents,sort_keys=False,width=4096))
            row.update(healthy_manifest=str(path.relative_to(root)),manifest_sha256=executor.sha(path))
        before=d['spec']['replicas'];after=replica_fault.desired_after_fault(before)
        row['construction'].update(fault_operator='decrement_replicas_clamp_zero',
            fault_decrement=2,fault_replicas=after,healthy_replicas_before_fault=before)
        row['development_revision']='Use the same decrement=2 operator and saturation at zero '
        row['development_revision']+='in the changed case and original-condition control; '
        row['development_revision']+='attained count differs only because healthy execution conditions differ.'
    # Separate design hypotheses: never feed them to the assessor or labeler.
    hypotheses={'written_at':time.time(),'main_trials':0,'not_outcome_labels':True,
        'document_alignment':'Section3.5.2: one potentially preserving and one invalidating '
            'or evidence-insufficient scenario per requirement, recorded before testing.',
        'P2-2-1':{'healthy_replicas':3,'required_minimum':2,'fault_after':1,
            'unchanged_action_arithmetic':2,'hypothesis':'potentially preserving; stronger criterion may need revalidation'},
        'P2-2-2':{'healthy_replicas':3,'required_minimum':3,'fault_after':1,
            'unchanged_action_arithmetic':2,'hypothesis':'potentially invalidating'},
        'original_control':{'healthy_replicas':1,'required_minimum':1,'fault_after':0,
            'unchanged_action_arithmetic':1},
        'preserved_development':'All v1-v3 traces stay unchanged, including accepted absolute-zero '
            'construction checks. They do not accept these revised P2 constructions.'}
    executor.save(out/'design-hypotheses.json',hypotheses)
    p['control_policy']='Every P2 pair uses desired=max(0,healthy_replicas-2), with identical '
    p['control_policy']+='decrement and saturation. The original control has healthy1/minimum1; '
    p['control_policy']+='the two P2 referenced-field variants have healthy3/minimum2 or3. '
    p['control_policy']+='Other families retain their unchanged identical faults. Full control '
    p['control_policy']+='repeat/order and shared-control analysis still require main freeze.'
    p['revisions_requiring_new_feasibility']=['P2-2-1','P2-2-2']
    p['P3_revision_construction_acceptance']='results/continuation-20261003/window-2/P3-2-1-acceptance.json'
    p['P3_revision_construction_acceptance_sha256']=executor.sha(root/p['P3_revision_construction_acceptance'])
    p['development_pilots']=[copy.deepcopy(x) for x in p['controls'] if x['id'].startswith('DEV-')]
    executor.save(out/'protocol.json',p);executor.freeze(out)
    print('Prospective P2 same-operator contrast prepared; all previous evidence preserved; main zero.')


if __name__=='__main__':prepare()

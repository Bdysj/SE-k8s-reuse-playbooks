"""Read-only collector for the explicitly admitted five-playbook subset."""
import copy
import hashlib
import json
import subprocess
import time
from urllib.parse import urlparse

import yaml
import main_verifier as v
import reset_baseline as rb
import reuse_assessor as assessor


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(candidate, current):
    snapshot = v.collect()
    snapshot['configmaps'] = rb.kjson('get','configmaps')['items']
    snapshot['horizontalpodautoscalers'] = rb.kjson('get','horizontalpodautoscalers')['items']
    snapshot['annotation_origin'] = current['annotation_origin']
    load = v.find(snapshot,'deployments','load')
    if load:
        containers=load['spec']['template']['spec']['containers']
        host=next((e.get('value') for c in containers for e in c.get('env',[]) if e['name']=='HOST'),None)
        if host:
            hostname=urlparse(host).hostname
            snapshot['client_frontend_service']=hostname.split('.')[0] if hostname else None
    snapshot['read_only_discovery'] = None
    if candidate == 'P1':
        args = ['kubectl','--context',rb.CONTEXT,'get','deployments','--namespace',rb.NS]
        result = subprocess.run(args,capture_output=True,text=True,timeout=30)
        if result.returncode == 0:
            # Same first-column/grep-web order as the frozen command; no new locator.
            names=[line.split()[0] for line in result.stdout.splitlines() if line.split()]
            snapshot['p1_discovery_names']=[name for name in names if 'web' in name]
        snapshot['read_only_discovery']={'argv':args,'filter':'first column, substring web, first match',
            'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
    if candidate == 'P3':
        nodes=json.loads(rb.kubectl('get','nodes','-o','json',namespaced=False))['items']
        d=v.find(snapshot,'deployments','web')
        if d:
            selector=d['spec']['template']['spec'].get('nodeSelector',{})
            eligible=[n for n in nodes if all(n['metadata'].get('labels',{}).get(k)==value for k,value in selector.items())]
            snapshot['p3_fixed_affinity_supported']=bool(eligible) and any(
                n['metadata'].get('labels',{}).get('beta.kubernetes.io/os')=='linux' for n in eligible)
            snapshot['p3_node_facts']=[{'name':n['metadata']['name'],'uid':n['metadata']['uid'],
                'labels':n['metadata'].get('labels',{}),'allocatable':n.get('status',{}).get('allocatable',{})} for n in eligible]
    if candidate == 'P4':
        baseline=[d for d in yaml.safe_load_all(rb.MANIFEST.read_text()) if d]
        original=next(d for d in baseline if d['kind']=='Deployment' and d['metadata']['name']=='web')
        d=v.find(snapshot,'deployments','web')
        if d:
            old=next(c for c in original['spec']['template']['spec']['containers'] if c['name']=='web')
            current_container=next((c for c in d['spec']['template']['spec']['containers'] if c['name']=='web'),None)
            fields=('image','command','args','volumeMounts','workingDir')
            snapshot['p4_registered_consumption_unchanged']=bool(current_container) and all(
                current_container.get(k)==old.get(k) for k in fields)
            snapshot['p4_consumption_provenance']={'manifest':str(rb.MANIFEST.relative_to(rb.ROOT)),
                'manifest_sha256':sha(rb.MANIFEST),'mapping':'registered web entrypoint envsubst at container startup',
                'automatic_comparison_fields':list(fields),'manual_semantics':'CATALOGUE_HOST consumed in nginx upstream'}
    snapshot['completed_at']=time.time()
    snapshot['relevant_revision']=relevant_revision(snapshot,candidate,current)
    return snapshot


def relevant_revision(snapshot,candidate,current):
    # An explicit dependency closure; unrelated application objects do not force
    # reassessment in the proposed method. The whole-environment baseline is broader.
    deployments={current['serving_deployment'],'ratings' if candidate=='P2' else 'web'}
    services={current['frontend_service'],'ratings' if candidate=='P2' else current['frontend_service']}
    if candidate=='P4' or current['cart_check']:
        deployments.update(['catalogue','cart','redis','mongodb']);services.update([current['catalogue_service'],'catalogue','cart','redis','mongodb'])
    if candidate=='P2' or current['vote_check']:
        deployments.update(['ratings','mysql','catalogue']);services.update(['ratings','mysql',current['catalogue_service']])
    selected={kind:[] for kind in v.KINDS}
    for kind in ('deployments','statefulsets'):
        selected[kind]=[x for x in snapshot[kind] if x['metadata']['name'] in deployments]
    uids={x['metadata']['uid'] for x in selected['deployments']}
    selected['replicasets']=[x for x in snapshot['replicasets'] if any(v.controlled_by(x,'Deployment',uid) for uid in uids)]
    rsuids={x['metadata']['uid'] for x in selected['replicasets']}
    statefuluids={x['metadata']['uid'] for x in selected['statefulsets']}
    selected['pods']=[x for x in snapshot['pods'] if any(v.controlled_by(x,'ReplicaSet',uid) for uid in rsuids)
                      or any(v.controlled_by(x,'StatefulSet',uid) for uid in statefuluids)]
    selected['services']=[x for x in snapshot['services'] if x['metadata']['name'] in services]
    selected['endpointslices']=[x for x in snapshot['endpointslices'] if x['metadata'].get('labels',{}).get('kubernetes.io/service-name') in services]
    def stable(obj):
        obj=copy.deepcopy(obj)
        obj.pop('status',None)
        for key in ['creationTimestamp','resourceVersion','managedFields']:
            obj.get('metadata',{}).pop(key,None)
        return obj
    value={kind:sorted([stable(x) for x in rows],key=lambda x:x['metadata']['name']) for kind,rows in selected.items()}
    value['p3_node_facts']=snapshot.get('p3_node_facts')
    value['contract_sha256']=assessor.digest(current)
    return assessor.digest(value)


def common_safety_gate(candidate,candidate_path,registered_hash,context,namespace,snapshot,allowed_workloads):
    failures=[]
    if candidate not in ['P1','P2','P3','P4','P5']:
        failures.append('outside admitted frozen candidate subset')
    if sha(candidate_path)!=registered_hash:
        failures.append('candidate code changed')
    if context not in ['k3d-lab','k3d-lab-validation'] or namespace!='robot-shop':
        failures.append('outside isolated experiment scope')
    if candidate=='P1':
        resolved=snapshot.get('p1_discovery_names')
        if resolved is None:
            failures.append('discovery scope not observable')
        elif resolved and resolved[0] not in allowed_workloads:
            failures.append('discovered target outside declared action scope')
    if candidate=='P2':
        d=v.find(snapshot,'deployments','ratings')
        if d is not None:
            count=d['spec'].get('replicas')
            if not isinstance(count,int) or isinstance(count,bool) or not 0<=count+1<=4:
                failures.append('replica action outside fixed 0..4 bound')
    return {'passed':not failures,'failures':failures,'action_timeout_seconds':120,
            'rollback':'confirmed seed-and-manifest reset outside evaluated window',
            'gate_failure_is_not_assessor_success':True}

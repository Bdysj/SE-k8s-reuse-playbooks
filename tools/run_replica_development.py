"""Keep the incident faulty while the unchanged candidate runs on its replica.

This is a prospectively scoped, development-only fidelity/capacity acceptance.
The adapter checks fidelity immediately after replica injection and delegates
all candidate execution/observation/reset logic to the unchanged v4 executor.
It never supplies a repair outcome label to the observer or assessor.
"""
import copy
import json
import subprocess
import threading
import time
import traceback

import audit_main_development as audit
import main_executor_v4 as ex
import main_verifier as verifier
import qualification as q
import registration_trials as reg
import reset_baseline as rb
import scenario_feasibility as scenarios
from prepare_replica_development import OUT,ROLE_MAP,translate

PRIMARY='k3d-lab'
REPLICA='k3d-lab-validation'
STATE=OUT/'capacity-fidelity.json'


def k(context,*args,namespace='robot-shop'):
    cmd=['kubectl','--context',context]+(['-n',namespace] if namespace else [])+list(args)
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
    if p.returncode:raise RuntimeError(' '.join(cmd[:7])+': '+p.stderr.strip()[:400])
    return p.stdout


def data(context):
    return {kind:json.loads(k(context,'get',kind,'-o','json'))['items'] for kind in verifier.KINDS}


def fingerprint(context):
    previous=rb.CONTEXT
    try:
        rb.CONTEXT=context
        return rb.live_fingerprint()
    finally:rb.CONTEXT=previous


def node_facts(context):
    objects=json.loads(k(context,'get','nodes','-o','json',namespace=None))['items']
    answer={}
    for obj in objects:
        name=obj['metadata']['name'];status=obj.get('status',{});labels=obj['metadata']['labels']
        answer[name]={'os':labels.get('kubernetes.io/os'),'arch':labels.get('kubernetes.io/arch'),
            'legacy_os':labels.get('beta.kubernetes.io/os'),
            'version':status.get('nodeInfo',{}).get('kubeletVersion'),
            'runtime':status.get('nodeInfo',{}).get('containerRuntimeVersion'),
            'allocatable':status.get('allocatable'), 'taints':obj['spec'].get('taints',[]),
            'conditions':{c['type']:c['status'] for c in status.get('conditions',[])}}
    return answer


def volumes(context):
    pvcs=json.loads(k(context,'get','pvc','-o','json'))['items']
    pvs=json.loads(k(context,'get','pv','-o','json',namespace=None))['items']
    bound={c['spec'].get('volumeName'):c for c in pvcs}
    answer={}
    for pv in pvs:
        if pv['metadata']['name'] not in bound:continue
        claim=bound[pv['metadata']['name']]
        answer[claim['metadata']['name']]={'claim_uid':claim['metadata']['uid'],
            'pv_uid':pv['metadata']['uid'],'volume_name':pv['metadata']['name'],
            'phase':claim.get('status',{}).get('phase'),'storage':claim['spec']['resources'],
            'access_modes':claim['spec']['accessModes'],'storage_class':claim['spec'].get('storageClassName'),
            'storage_path':pv['spec'].get('hostPath',pv['spec'].get('local',{})).get('path'),
            'node_affinity':pv['spec'].get('nodeAffinity')}
    return answer


def docker_roots():
    names=[ctx+'-'+role for ctx in (PRIMARY,REPLICA) for role in ('server-0','agent-0','agent-1')]
    objects=json.loads(subprocess.check_output(['docker','inspect',*names],text=True,timeout=30))
    return {o['Name'].lstrip('/'):{'container_id':o['Id'],
        'storage_volume':next(m['Name'] for m in o['Mounts'] if m['Destination']=='/var/lib/rancher/k3s')}
        for o in objects}


def target_and_non_target(context,row):
    observed=data(context)
    cfg=row['construction'];target=verifier.workload(observed,cfg['target_deployment'])
    non_targets={name:verifier.workload(observed,name) for name in row['non_target_workloads']}
    return {'at':time.time(),'target':target,
            'criterion_met':target['healthy'] and target['desired']>=cfg['required_replicas'],
            'endpoint_binding':verifier.endpoint_binding(observed,'ratings',cfg['target_deployment']),
            'non_targets':non_targets,
            'passed':target['desired']==cfg['fault_replicas'] and target['healthy']
                and all(x['healthy'] for x in non_targets.values())}


def primary_monitor(stop,row,record):
    # All calls specify the incident context; do not use shared mutable rb.CONTEXT.
    while not stop.is_set():
        try:
            sample=target_and_non_target(PRIMARY,row)
            if sample['criterion_met'] or not sample['passed']:
                raise RuntimeError('Incident changed or a named non-target workload became unhealthy')
            record['primary_overlap_samples'].append(sample)
            record['resource_samples'].append(resource_sample())
        except Exception as error:
            record['primary_monitor_errors'].append({'at':time.time(),'error':str(error)})
            return
        stop.wait(10)


def resource_sample():
    raw=subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}'],text=True,timeout=30)
    rows=[json.loads(x) for x in raw.splitlines() if x.strip()]
    selected=[r for r in rows if r['Name'].startswith('k3d-lab')]
    mem=subprocess.check_output(['docker','exec','k3d-lab-server-0','cat','/proc/meminfo'],text=True,timeout=30)
    memory={line.split(':')[0]:int(line.split()[1]) for line in mem.splitlines()
            if line.split(':')[0] in ('MemTotal','MemAvailable','SwapTotal','SwapFree')}
    return {'at':time.time(),'docker':selected,'other_running_containers':[r['Name'] for r in rows if r not in selected],
            'cpu_percent_total':sum(float(r['CPUPerc'].rstrip('%')) for r in selected),
            'vm_memory_kib':memory}


def stable_sample():
    result=resource_sample();result['clusters']={}
    for context in (PRIMARY,REPLICA):
        observed=data(context)
        workloads={name:verifier.workload(observed,name) for name in rb.ALL_WORKLOADS}
        chaos=json.loads(k(context,'get','pods','-o','json',namespace='chaos-mesh'))['items']
        load=json.loads(k(context,'exec','deploy/load','--','cat','/tmp/load-metrics.json'))
        chaos_ready=lambda o:bool(o.get('status',{}).get('containerStatuses')) and all(
            c.get('ready') is True for c in o['status']['containerStatuses']) and not o['metadata'].get('deletionTimestamp')
        result['clusters'][context]={'workloads':workloads,'load':load,
            'chaos':sorted([{'name':o['metadata']['name'],'ready':chaos_ready(o),
                'restarts':sum(c.get('restartCount',0) for c in o.get('status',{}).get('containerStatuses',[]))} for o in chaos],key=lambda x:x['name'])}
        if set(workloads)!=set(rb.ALL_WORKLOADS) or not all(w['healthy'] and w['desired']==1 for w in workloads.values()):
            raise RuntimeError(context+' current baseline workload set/ownership/readiness failed')
        if len(chaos)!=5 or not all(chaos_ready(o) for o in chaos):
            raise RuntimeError(context+' Chaos Mesh system readiness failed')
        if load['users']!=5:raise RuntimeError(context+' declared client count changed')
    return result


def accept_stability(samples,seconds):
    answer={}
    if not samples or samples[-1]['elapsed_seconds']<seconds:
        raise RuntimeError('Incomplete stability window')
    for context in (PRIMARY,REPLICA):
        first=samples[0]['clusters'][context];last=samples[-1]['clusters'][context]
        if any(s['clusters'][context]['chaos']!=first['chaos'] for s in samples):
            raise RuntimeError('Chaos Mesh Pod/restart change during stability: '+context)
        intentional=lambda m:sum(e['failures'] for e in m['entries'] if '[upstream intentional error]' in e['name'])
        initial=first['load'];final=last['load']
        requests=final['requests']-initial['requests'];failures=final['failures']-initial['failures']
        expected=intentional(final)-intentional(initial)
        answer[context]={'requests':requests,'raw_failures':failures,
            'explicit_upstream_intentional_failures':expected,'other_failures':failures-expected,
            'max_load_rss_bytes':max(s['clusters'][context]['load']['rss_bytes'] for s in samples)}
        if requests<=0 or failures-expected!=0:
            raise RuntimeError('Concurrent declared load failed: '+context+' '+str(answer[context]))
    return answer


def main():
    if STATE.exists():raise RuntimeError('Preserve an existing pilot attempt')
    p=ex.inputs(OUT);plan=json.loads((OUT/'pilot-plan.json').read_text())
    if ex.sha(OUT/'protocol.json')!=plan['executor_protocol_sha256']:raise RuntimeError('Plan drift')
    for name,h in plan['source_hashes'].items():
        if ex.sha(ex.ROOT/'tools'/name)!=h:raise RuntimeError('Pilot source changed: '+name)
    primary=ex.inputs(ex.ROOT/'results/pre-main-development-v4')
    row_primary=next(r for r in primary['scenarios'] if r['id']==plan['scenario'])
    row=p['scenarios'][0]
    record={'type':'current_v3_replica_fidelity_and_capacity_development','main_trials':0,
        'started_at':time.time(),'plan_sha256':ex.sha(OUT/'pilot-plan.json'),
        'primary_overlap_samples':[],'primary_monitor_errors':[],'resource_samples':[],
        'stability_samples':[],'passed':False,'scope_limit':plan['scope_limit']}
    stop=threading.Event();thread=None;original_inject=ex.inject_current
    primary_started=False
    try:
        print('Fresh reset and prospective incident setup on primary',flush=True)
        reg.reset_s4(OUT/'primary-initial-reset.log',primary)
        rb.CONTEXT=PRIMARY;rb.MANIFEST=ex.ROOT/primary['manifest'];rb.NS=primary['namespace']
        rb.ensure_probe();ex.apply_variant(row_primary,ex.ROOT/row_primary['healthy_manifest'])
        deadline=time.monotonic()+90
        while True:
            health=scenarios.healthy(row_primary,ex.ROOT/row_primary['healthy_manifest'])
            record.setdefault('primary_healthy_setup',[]).append(health)
            if health['passed']:break
            if time.monotonic()>=deadline:raise RuntimeError('Primary variant setup failed')
            time.sleep(5)
        start=time.monotonic()
        while True:
            health=verifier.target(row_primary);nt=verifier.non_target(row_primary)
            record.setdefault('primary_healthy_window',[]).append({'target':health,'non_target':nt})
            if not health['healthy'] or not nt['passed']:raise RuntimeError('Primary healthy window failed')
            if time.monotonic()-start>=primary['timing']['healthy_window']:break
            time.sleep(5)
        record['primary_injection']=original_inject(row_primary);primary_started=True
        deadline=time.monotonic()+90
        while True:
            health=verifier.target(row_primary)
            record.setdefault('primary_fault_confirmation',[]).append(health)
            if not health['healthy'] and health['workload']['healthy']:break
            if time.monotonic()>=deadline:raise RuntimeError('Primary fault confirmation failed')
            time.sleep(3)
        record['overlap_started_at']=time.time()
        thread=threading.Thread(target=primary_monitor,args=(stop,row_primary,record),daemon=True);thread.start()
        print('Primary fault active; replica reset/replay runs concurrently',flush=True)
        rb.CONTEXT=REPLICA;rb.MANIFEST=ex.ROOT/p['manifest'];rb.FINGERPRINT=rb.MANIFEST.with_suffix('.fingerprint.json')

        def fidelity_injection(replica_row):
            injection=original_inject(replica_row)
            # Wait for the expected scale-down before comparing actual serving state.
            end=time.monotonic()+90
            while True:
                state=target_and_non_target(REPLICA,replica_row)
                if state['passed'] and not state['criterion_met']:break
                if time.monotonic()>=end:raise RuntimeError('Replica fault state did not settle')
                time.sleep(3)
            primary_fp=fingerprint(PRIMARY);replica_fp=fingerprint(REPLICA)
            differences=rb.diff_fingerprint(translate(primary_fp),replica_fp)
            nodes_primary=node_facts(PRIMARY);nodes_replica=node_facts(REPLICA)
            node_equal=translate(nodes_primary)==nodes_replica
            pvc_primary=volumes(PRIMARY);pvc_replica=volumes(REPLICA);roots=docker_roots()
            separation=(set(pvc_primary)==set(pvc_replica)=={'mysql-data','data-redis-0'}
                and all(pvc_primary[n]['claim_uid']!=pvc_replica[n]['claim_uid']
                    and pvc_primary[n]['pv_uid']!=pvc_replica[n]['pv_uid']
                    and pvc_primary[n]['phase']==pvc_replica[n]['phase']=='Bound'
                    and pvc_primary[n]['storage']==pvc_replica[n]['storage']
                    and pvc_primary[n]['access_modes']==pvc_replica[n]['access_modes']
                    and pvc_primary[n]['storage_class']==pvc_replica[n]['storage_class'] for n in pvc_primary)
                and len({r['storage_volume'] for r in roots.values()})==6)
            fault_same=(record['primary_injection']['before_replicas']==injection['before_replicas']
                        and record['primary_injection']['replicas']==injection['replicas']
                        and record['primary_injection']['decrement']==injection['decrement'])
            receipt={'at':time.time(),'primary_fingerprint':primary_fp,'replica_fingerprint':replica_fp,
                'differences':differences,'primary_nodes':nodes_primary,'replica_nodes':nodes_replica,
                'node_facts_equal_after_role_mapping':node_equal,'primary_pvcs':pvc_primary,
                'replica_pvcs':pvc_replica,'docker_storage_roots':roots,
                'writable_storage_separate':separation,'same_attained_fault':fault_same,
                'replica_fault_state':state,
                'passed':not differences and node_equal and separation and fault_same}
            ex.save(OUT/'fault-state-fidelity.json',receipt)
            record['fidelity_receipt_sha256']=ex.sha(OUT/'fault-state-fidelity.json')
            if not receipt['passed']:raise RuntimeError('Replica fidelity not confirmed; candidate blocked')
            if record['primary_monitor_errors']:raise RuntimeError('Incident monitor failed before candidate')
            injection['fidelity_receipt_sha256']=record['fidelity_receipt_sha256']
            print('Same fault, mapped node facts and separate data confirmed',flush=True)
            return injection

        ex.inject_current=fidelity_injection
        path=OUT/(row['id']+'-repair-attempt-1')
        r=ex.run_case(row,'repair',path,p)
        accepted=audit.audit(path/'record.json');ex.save(OUT/'replica-executor-acceptance.json',accepted)
        record.update(replica_record=str((path/'record.json').relative_to(ex.ROOT)),
            replica_record_sha256=ex.sha(path/'record.json'),replica_executor_accepted=accepted['accepted'],
            replica_repair_outcome=r['repair_outcome'])
        if not accepted['accepted']:raise RuntimeError('Replica executor evidence incomplete: '+str(accepted['failures']))
        if record['primary_monitor_errors']:raise RuntimeError('Incident changed during replica replay')
        current=fingerprint(PRIMARY)
        if current!=json.loads((OUT/'fault-state-fidelity.json').read_text())['primary_fingerprint']:
            raise RuntimeError('Incident declarative state changed during replica replay')
        record['incident_unchanged_by_replica_replay']=True
        record['overlap_completed_at']=time.time()
        stop.set();thread.join(timeout=35)
        if thread.is_alive():raise RuntimeError('Incident monitor did not terminate')
        print('Isolated replay complete; restoring primary before concurrent stability window',flush=True)
        reg.reset_s4(OUT/'primary-post-reset.log',primary);record['primary_post_reset_confirmed']=True
        rb.CONTEXT=PRIMARY;rb.MANIFEST=ex.ROOT/primary['manifest'];rb.FINGERPRINT=rb.MANIFEST.with_suffix('.fingerprint.json')
        # Warmup is fixed before taking the first cumulative load counter.
        warm=time.monotonic()
        while time.monotonic()-warm<plan['warmup_seconds']:time.sleep(5)
        start=time.monotonic()
        while True:
            sample=stable_sample();sample['elapsed_seconds']=time.monotonic()-start
            record['stability_samples'].append(sample);ex.save(STATE,record)
            print('Concurrent stability %.1f/%ss; both current-owner baselines healthy' %
                  (sample['elapsed_seconds'],plan['stability_seconds']),flush=True)
            if sample['elapsed_seconds']>=plan['stability_seconds']:break
            time.sleep(min(plan['sample_interval_seconds'],plan['stability_seconds']-sample['elapsed_seconds']))
        record['load_summary']=accept_stability(record['stability_samples'],plan['stability_seconds'])
        record['passed']=record['incident_unchanged_by_replica_replay'] and record['primary_post_reset_confirmed']
    except Exception as error:
        record.update(error=str(error),traceback=traceback.format_exc(),passed=False)
    finally:
        ex.inject_current=original_inject
        stop.set()
        if thread is not None:thread.join(timeout=35)
        # Only a full reset can accept the primary boundary, even on a rejection.
        if primary_started and not record.get('primary_post_reset_confirmed'):
            try:
                reg.reset_s4(OUT/'primary-rejection-reset.log',primary)
                record['primary_post_reset_confirmed']=True
            except Exception as error:record['primary_reset_error']=str(error)
        record['completed_at']=time.time();ex.save(STATE,record)
        print(json.dumps({k:record.get(k) for k in ('passed','error','replica_repair_outcome',
              'replica_executor_accepted','incident_unchanged_by_replica_replay','primary_post_reset_confirmed','load_summary')}),flush=True)
    if not record['passed']:raise SystemExit(1)


if __name__=='__main__':main()

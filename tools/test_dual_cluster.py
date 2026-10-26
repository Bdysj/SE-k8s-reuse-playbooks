#!/usr/bin/env python3
"""Capacity/functional pilot with two full three-node clusters, not RQ3b results.

Assumes lab-validation has been created and all identical images imported. Makes
one replica adjustment fault in lab while restoring/serving a separate snapshot
in lab-validation. Restore/bootstrap costs are measured but remain pilot timings.
"""
import argparse,hashlib,json,subprocess,sys,time
from pathlib import Path
import reset_baseline as rb
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'results/dual-cluster';OUT.mkdir(parents=True,exist_ok=True)
ap=argparse.ArgumentParser();ap.add_argument('--stability-seconds',type=int,default=1800);args=ap.parse_args()
record={'type':'dual_cluster_capacity_pilot','main_RQ3b_trials':0,
        'incident_context':'k3d-lab','validation_context':'k3d-lab-validation',
        'topology':'one server and two agents in each cluster',
        'shared_writable_volume':False,'stability_seconds_requested':args.stability_seconds,'samples':[]}
record['input_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'manifests/baseline.arm64.yaml',ROOT/'manifests/baseline.arm64.fingerprint.json',ROOT/'candidates/P2.yaml',ROOT/'sources/sources.lock.yaml']}
record['bootstrap_sampling_note']='Images and database bootstrap are counted in the pilot log; cache warmth differs from the original cluster. Timings are not formal RQ3b cost estimates.'


def k(context,*args):
    p=subprocess.run(['kubectl','--context',context,'-n','robot-shop',*args],capture_output=True,text=True,timeout=120)
    if p.returncode:raise RuntimeError(p.stderr.strip())
    return p.stdout


def snapshot():
    out=subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}'],text=True)
    rows=[json.loads(line) for line in out.splitlines() if line.strip()]
    rows=[r for r in rows if r['Name'].startswith('k3d-lab')]
    state={ctx:json.loads(k(ctx,'get','pods','-o','json'))['items'] for ctx in ['k3d-lab','k3d-lab-validation']}
    summary={ctx:[{'name':p['metadata']['name'],'ready':bool(p.get('status',{}).get('containerStatuses')) and all(c.get('ready') for c in p.get('status',{}).get('containerStatuses',[])),
                  'restarts':sum(c.get('restartCount',0) for c in p.get('status',{}).get('containerStatuses',[]))}
                  for p in pods if p['metadata'].get('labels',{}).get('service')] for ctx,pods in state.items()}
    mem = subprocess.check_output(['docker','exec','k3d-lab-server-0','cat','/proc/meminfo'],text=True)
    fields = {line.split(':')[0]:int(line.split()[1]) for line in mem.splitlines() if line.split(':')[0] in ('MemTotal','MemAvailable','SwapTotal','SwapFree')}
    chaos={}
    for ctx in state:
        items=json.loads(subprocess.check_output(['kubectl','--context',ctx,'-n','chaos-mesh','get','pods','-o','json'],text=True,timeout=30))['items']
        chaos[ctx]=[{'name':p['metadata']['name'],'ready':bool(p.get('status',{}).get('containerStatuses')) and all(c.get('ready') for c in p.get('status',{}).get('containerStatuses',[])), 'restarts':sum(c.get('restartCount',0) for c in p.get('status',{}).get('containerStatuses',[]))} for p in items]
    loads={ctx:json.loads(k(ctx,'exec','deploy/load','--','cat','/tmp/load-metrics.json')) for ctx in state}
    return {'time':time.time(),'docker_stats':rows,'vm_memory_kib':fields,'workloads':summary,'load_metrics':loads,'chaos_pods':chaos}

try:
    k('k3d-lab','scale','deployment','ratings','--replicas=0')
    k('k3d-lab','wait','--for=delete','pod','-l','service=ratings','--timeout=90s')
    record['incident_fault']='ratings replicas=0, remains in place while validation snapshot boots'
    t=time.monotonic()
    with (OUT/'replica-reset.log').open('w') as f:
        p=subprocess.Popen([sys.executable,'-u',str(ROOT/'tools/reset_baseline.py'),'--context','k3d-lab-validation','--tier-timeout','600'],stdout=f,stderr=subprocess.STDOUT)
        while p.poll() is None:
            # Only lightweight resource inventory during bootstrap; functional samples follow.
            raw=subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}'],text=True)
            record['samples'].append({'time':time.time(),'bootstrap':True,'docker_stats':[json.loads(x) for x in raw.splitlines() if x.strip() and 'k3d-lab' in x]})
            (OUT/'summary.json').write_text(json.dumps(record,indent=2))
            time.sleep(10)
    record['replica_reset_seconds']=round(time.monotonic()-t,2);record['replica_reset_exit_code']=p.returncode
    if p.returncode:raise RuntimeError('validation replica reset failed; see replica-reset.log')
    before=int(k('k3d-lab','get','deployment','ratings','-o','jsonpath={.spec.replicas}'))
    other=int(k('k3d-lab-validation','get','deployment','ratings','-o','jsonpath={.spec.replicas}'))
    assert before==0 and other==1,(before,other)
    record['isolated_configuration_confirmed']=True
    # Inject and replay the same candidate ONLY in the validation context.
    k('k3d-lab-validation','scale','deployment','ratings','--replicas=0')
    t=time.monotonic()
    with (OUT/'P2-replica-ansible.log').open('w') as f:
        p=subprocess.run(['uv','run','--with','ansible-core==2.19.3','ansible-playbook','-i',str(ROOT/'tools/ansible-inventory.ini'),
                          str(ROOT/'candidates/P2.yaml'),'-e','kube_context=k3d-lab-validation'],stdout=f,stderr=subprocess.STDOUT,timeout=240)
    record['replica_candidate_seconds']=round(time.monotonic()-t,2);record['replica_candidate_exit_code']=p.returncode
    k('k3d-lab-validation','rollout','status','deployment/ratings','--timeout=120s')
    assert k('k3d-lab','get','deployment','ratings','-o','jsonpath={.spec.replicas}')=='0'
    record['incident_unchanged_by_replica_repair']=True
    record['replay_overlap_sample']=snapshot()
    record['volume_identity']={ctx:json.loads(k(ctx,'get','pvc','mysql-data','-o','json'))['spec']['volumeName'] for ctx in ['k3d-lab','k3d-lab-validation']}
    assert len(set(record['volume_identity'].values()))==2,record['volume_identity']
    k('k3d-lab','scale','deployment','ratings','--replicas=1')
    k('k3d-lab','rollout','status','deployment/ratings','--timeout=120s')
    stability_start=time.monotonic();print('Both complete environments running; starting sustained load/OOM check',flush=True)
    while time.monotonic()-stability_start < args.stability_seconds:
        sample=snapshot();record['samples'].append(sample)
        for ctx,pods in sample['chaos_pods'].items():
            assert len(pods)==5 and all(pod['ready'] and pod['restarts']==0 for pod in pods),(ctx,pods)
        for ctx,pods in sample['workloads'].items():
            assert len(pods)==13,(ctx,len(pods))
            assert all(pod['ready'] and pod['restarts']==0 for pod in pods),(ctx,pods)
            assert sample['load_metrics'][ctx]['users']==5,(ctx,sample['load_metrics'][ctx])
        (OUT/'summary.json').write_text(json.dumps(record,indent=2))
        elapsed=round(time.monotonic()-stability_start);print(f'Stability {elapsed}/{args.stability_seconds}s; both clusters ready, zero restarts',flush=True)
        time.sleep(min(30,max(0,args.stability_seconds-(time.monotonic()-stability_start))))
    record['stability_seconds_observed']=round(time.monotonic()-stability_start,2)
    stable=[s for s in record['samples'] if 'workloads' in s]
    record['load_stability_summary']={}
    for ctx in ['k3d-lab','k3d-lab-validation']:
        first=stable[0]['load_metrics'][ctx];last=stable[-1]['load_metrics'][ctx]
        intentional=lambda m:sum(e['failures'] for e in m['entries'] if '[upstream intentional error]' in e['name'])
        failures=last['failures']-first['failures'];expected=intentional(last)-intentional(first)
        record['load_stability_summary'][ctx]={'request_delta':last['requests']-first['requests'],'raw_failure_delta':failures,'upstream_intentional_failure_delta':expected,'other_failure_delta':failures-expected,'max_rss_bytes':max(s['load_metrics'][ctx]['rss_bytes'] for s in stable),'max_stats_entries':max(s['load_metrics'][ctx]['stats_entries'] for s in stable)}
    with (OUT/'incident-verify.log').open('w') as f:
        primary_verify=subprocess.run([sys.executable,'-u',str(ROOT/'tools/reset_baseline.py'),'--context','k3d-lab','--verify'],stdout=f,stderr=subprocess.STDOUT)
    record['incident_verify_exit_code']=primary_verify.returncode
    with (OUT/'replica-verify.log').open('w') as f:
        p=subprocess.run([sys.executable,'-u',str(ROOT/'tools/reset_baseline.py'),'--context','k3d-lab-validation','--verify'],stdout=f,stderr=subprocess.STDOUT)
    record['replica_verify_exit_code']=p.returncode
    record['passed']=record['replica_candidate_exit_code']==0 and p.returncode==0 and primary_verify.returncode==0 and all(x['other_failure_delta']==0 for x in record['load_stability_summary'].values()) and all(
        all(pod['ready'] and pod['restarts']==0 for pod in s['workloads']['k3d-lab-validation'])
        for s in record['samples'] if 'workloads' in s)
except Exception as e:
    record.update(passed=False,error=str(e))
finally:
    # Restore the incident target without touching the replica or its work volume.
    try:
        k('k3d-lab','scale','deployment','ratings','--replicas=1')
        k('k3d-lab','rollout','status','deployment/ratings','--timeout=120s')
    except Exception as cleanup_error:
        record['cleanup_error']=str(cleanup_error)
        record['passed']=False
    (OUT/'summary.json').write_text(json.dumps(record,indent=2))
    print(json.dumps({k:v for k,v in record.items() if k!='samples'},indent=2))
if not record.get('passed'):sys.exit(1)

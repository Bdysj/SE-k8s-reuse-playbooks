#!/usr/bin/env python3
"""Execute independent full-window S2 arms with raw evidence and controlled resets."""
import argparse,hashlib,json,re,shlex,subprocess,sys,time,traceback
from pathlib import Path
import ansible_runner
import reset_baseline as rb
from inject_fault import inject

ROOT=rb.ROOT
OUT=ROOT/'results/qualification-v1'
FAMILY={'P1':'web','P2':'ratings','P3':'web','P4':'web','P5':'web'}
PATHS={'P1':('/',r"Stan's Robot Shop"),'P2':('/api/ratings/api/fetch/Watson',r'"rating_count"\s*:\s*\d+'),
       'P3':('/api/catalogue/products',r'"sku"\s*:'),'P4':('/api/catalogue/products',r'"sku"\s*:'),
       'P5':('/',r"Stan's Robot Shop")}
DIRECT=[('catalogue','http://catalogue:8080/products',r'"sku"\s*:'),
        ('shipping','http://shipping:8080/codes',r'"code"\s*:'),
        ('user','http://user:8080/uniqueid',r'"uuid"\s*:'),
        ('payment','http://payment:8080/health',r'OK'),
        ('ratings','http://ratings:80/api/fetch/Watson',r'"rating_count"\s*:\s*\d+')]

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2))

def request(url,pattern,latency=1.0):
    marker='===STATUS==='
    cmd='curl -s --max-time 4 -w '+shlex.quote(marker+'%{http_code} %{time_total}')+' '+shlex.quote(url)+'; r=$?; printf " ===CURL_RC===%s" "$r"'
    p=subprocess.run(['kubectl','--context',rb.CONTEXT,'-n',rb.NS,'exec','probe','--','sh','-c',cmd],capture_output=True,text=True,timeout=20)
    if p.returncode:raise RuntimeError('Evidence collection failed: '+p.stderr[:300])
    body,sep,tail=p.stdout.rpartition(marker)
    if not sep or '===CURL_RC===' not in tail:raise RuntimeError('Malformed probe result')
    stat,rc=tail.split('===CURL_RC===');bits=stat.split()
    code=int(bits[0]);seconds=float(bits[1]);curl_rc=int(rc.strip())
    content=bool(re.search(pattern,body,re.S))
    return {'at':time.time(),'status':code,'curl_rc':curl_rc,'seconds':seconds,'content_match':content,
            'healthy':curl_rc==0 and code==200 and content and seconds<=latency,'body_prefix':body[:120]}

def ready(p):
    c=p.get('status',{}).get('containerStatuses') or []
    return bool(c) and all(x.get('ready') and x.get('restartCount',0)==0 for x in c) and not p['metadata'].get('deletionTimestamp')

def current_rollout(workload):
    d=rb.kjson('get','deployment',workload)
    def template(t):
        labels={k:v for k,v in t['metadata'].get('labels',{}).items() if k!='pod-template-hash'}
        return {'labels':labels,'annotations':t['metadata'].get('annotations',{}),'spec':t['spec']}
    rs=[r for r in rb.kjson('get','replicasets')['items'] if any(o.get('controller') and o.get('kind')=='Deployment' and
        o.get('uid')==d['metadata']['uid'] for o in r['metadata'].get('ownerReferences',[]))]
    current=[r for r in rs if template(r['spec']['template'])==template(d['spec']['template'])]
    current_uids={r['metadata']['uid'] for r in current}
    pods=[p for p in rb.kjson('get','pods')['items'] if any(o.get('controller') and o.get('kind')=='ReplicaSet' and
            o.get('uid') in current_uids for o in p['metadata'].get('ownerReferences',[])) and not p['metadata'].get('deletionTimestamp')]
    desired=d['spec']['replicas'];st=d.get('status',{})
    complete=(desired>0 and len(current)==1 and st.get('observedGeneration')==d['metadata']['generation']
              and st.get('updatedReplicas',0)==desired and st.get('readyReplicas',0)==desired
              and st.get('availableReplicas',0)==desired and len(pods)==desired and all(ready(p) for p in pods))
    return {'deployment_uid':d['metadata']['uid'],'generation':d['metadata']['generation'],'observed_generation':st.get('observedGeneration'),
            'desired':desired,'current_replicasets':[{'name':r['metadata']['name'],'uid':r['metadata']['uid']} for r in current],
            'pods':[{'name':p['metadata']['name'],'uid':p['metadata']['uid'],'ready_zero_restarts':ready(p)} for p in pods],
            'complete':complete}

def observe(key):
    path,pattern=PATHS[key];o=request('http://web:8080'+path,pattern)
    if key=='P3':
        o['rollout']=current_rollout(FAMILY[key]);o['healthy']=o['healthy'] and o['rollout']['complete']
    return o

def non_target(key):
    target=FAMILY[key]
    checks=[{'name':n,**request(url,pattern,4.0)} for n,url,pattern in DIRECT if n!=target]
    pods=[p for p in rb.kjson('get','pods')['items'] if p['metadata'].get('labels',{}).get('service') not in (None,target)]
    bad=[p['metadata']['name'] for p in pods if not ready(p)]
    return {'at':time.time(),'checks':checks,'unhealthy_pods':bad,'passed':all(c['healthy'] for c in checks) and not bad}

def business():
    checks=[{'name':n,**request('http://web:8080'+path,pattern,4.0)} for n,path,code,pattern in rb.CHECKS]
    return {'at':time.time(),'checks':checks,'passed':all(c['healthy'] for c in checks)}

def reset(log,protocol):
    with log.open('w') as f:
        p=subprocess.run([sys.executable,'-u',str(ROOT/'tools/reset_baseline.py'),'--manifest',protocol['manifest'],'--tier-timeout','600'],stdout=f,stderr=subprocess.STDOUT,timeout=1500)
    if p.returncode:raise RuntimeError('Baseline reset failed: '+str(log))

def inject_candidate(key):
    target=FAMILY[key]
    if key=='P1':return inject('pod-failure',target,target)
    if key=='P2':
        rb.kubectl('scale','deployment',target,'--replicas=0');return {'kind':'replicas=0','workload':target}
    if key=='P3':return inject('pod-config-error',target,target,resource_values={'requests':{'cpu':'1m','memory':'1Mi'},'limits':{'cpu':'2m','memory':'1Mi'}})
    if key=='P4':
        rb.kubectl('set','env','deployment/web','CATALOGUE_HOST=user')
        rb.kubectl('rollout','status','deployment/web','--timeout=120s')
        return {'kind':'consumed CATALOGUE_HOST=user'}
    rb.kubectl('patch','service','web','--type=merge','-p','{"spec":{"selector":{"service":"missing-pilot-target"}}}')
    return {'kind':'wrong web Service selector only'}

def start_runner(key,path):
    (path/'runner').mkdir(parents=True,exist_ok=True)
    return ansible_runner.run_async(private_data_dir=str(path/'runner'),project_dir=str(ROOT),
        playbook='candidates/'+key+'.yaml',inventory=str(ROOT/'tools/ansible-inventory.ini'),
        extravars={'kube_context':rb.CONTEXT},quiet=True,process_isolation=False,
        settings={'job_timeout':120,'idle_timeout':120},envvars={'ANSIBLE_HOST_KEY_CHECKING':'False'})

def execution_result(runner):
    events=list(runner.events)
    terminals=[e for e in events if e['event'] in ['runner_on_ok','runner_on_failed','runner_on_unreachable','runner_on_skipped']]
    failures=[e for e in terminals if e['event'] in ['runner_on_failed','runner_on_unreachable']]
    mutations=[e for e in terminals if e.get('event_data',{}).get('res',{}).get('changed')]
    stats=runner.stats or {}
    complete=(runner.rc==0 and runner.status=='successful' and bool(stats) and not failures and bool(mutations))
    return {'rc':runner.rc,'status':runner.status,'stats':stats,'task_events':[{'event':e['event'],'task':e.get('event_data',{}).get('task'),
        'rc':e.get('event_data',{}).get('res',{}).get('rc'),'changed':e.get('event_data',{}).get('res',{}).get('changed')} for e in terminals],
        'execution_complete':complete}

def run_arm(key,arm,path,protocol,confirmed_reset=None):
    record={'candidate':key,'arm':arm,'type':'S2_independent_matched_arm','registered':False,'S4_trials':0,'main_trials':0,
            'started_at':time.time(),'protocol_sha256':sha(OUT/'protocol.json'),'samples':[],
            'execution_complete':None,'target_recovery':None,'non_target_regression':False}
    file=path/'record.json';save(file,record)
    thread=runner=None
    try:
        if confirmed_reset is None:
            reset(path/'initial-reset.log',protocol)
        else:
            # The just-confirmed boundary reset is both the previous arm's
            # teardown and this arm's setup; do not restore it twice.
            save(path/'initial-reset-receipt.json',{'source':str(confirmed_reset.relative_to(ROOT)),
                'sha256':sha(confirmed_reset),'boundary_reset_confirmed':True})
        rb.ensure_probe()
        warm_start=time.monotonic()
        record['healthy_warmup']=[]
        while True:
            o=observe(key);record['healthy_warmup'].append(o)
            if not o['healthy']:raise RuntimeError('Healthy-state target check failed')
            if time.monotonic()-warm_start>=30:break
            time.sleep(5)
        record['initial_non_target']=non_target(key)
        if not record['initial_non_target']['passed']:raise RuntimeError('Healthy-state non-target checks failed')
        record['fault_activation_at']=time.time();activation_mono=time.monotonic()
        record['injection']=inject_candidate(key)
        record['fault_expiry_expected_at']=record['fault_activation_at']+1200 if key=='P1' else None
        confirm_deadline=time.monotonic()+90;confirm=[]
        while True:
            o=observe(key);confirm.append(o)
            if not o['healthy']:break
            if time.monotonic()>=confirm_deadline:raise RuntimeError('Fault was not observable')
            time.sleep(3)
        record['fault_confirmation']=confirm;start=time.monotonic();record['window_start_at']=time.time()
        recovery_start=None;regression=False;pre=non_target(key)
        record['non_target_before_execution']=pre
        if not pre['passed']:raise RuntimeError('Named non-target checks do not pass before execution')
        last_full=0
        while True:
            elapsed=time.monotonic()-start
            if arm=='repair' and runner is None and elapsed>=60:
                record['repair_start_seconds']=elapsed;record['repair_started_at']=time.time()
                thread,runner=start_runner(key,path)
            o=observe(key);o['window_seconds']=time.monotonic()-start
            record['samples'].append(o)
            if o['window_seconds']<60 and o['healthy']:raise RuntimeError('Fault self-resolved before repair')
            if arm=='no-repair' and o['healthy']:raise RuntimeError('Fault self-resolved in full no-repair window')
            if arm=='repair' and runner is not None and not thread.is_alive():
                record['execution']=execution_result(runner)
                record['execution_complete']=record['execution']['execution_complete']
                if not record['execution']['execution_complete']:raise RuntimeError('Necessary Ansible execution failed')
                if o['healthy']:
                    if recovery_start is None:recovery_start=o['window_seconds']
                elif recovery_start is not None:raise RuntimeError('Recovery did not persist')
            if arm=='repair' and runner is not None and o['window_seconds']-record['repair_start_seconds']>120 and recovery_start is None:
                raise RuntimeError('Repair exceeded the fixed recovery deadline')
            if elapsed-last_full>=15 or o['window_seconds']>=210:
                nt=non_target(key);record.setdefault('non_target_observations',[]).append(nt)
                regression=regression or not nt['passed']
                record['non_target_regression']=regression
                if recovery_start is not None:
                    b=business();record.setdefault('recovered_business',[]).append(b)
                    if not b['passed']:
                        record['business_window_failed']=True
                last_full=elapsed
            save(file,record)
            if o['window_seconds']>=210:break
            time.sleep(min(5,max(0,210-(time.monotonic()-start))))
        record['window_end_at']=time.time();record['window_observed_seconds']=time.monotonic()-start
        record['non_target_regression']=regression
        if key=='P1':
            record['chaos_at_window_end']=rb.kjson('get','podchaos','pilot-pod-fail')
            record['finished_before_fault_expiry']=time.monotonic()-activation_mono<1200
            if not record['finished_before_fault_expiry']:raise RuntimeError('Timed fault expired during evaluation')
        if arm=='repair':
            record['sustained_recovery_seconds']=None if recovery_start is None else record['samples'][-1]['window_seconds']-recovery_start
            record['target_recovery']=recovery_start is not None and record['sustained_recovery_seconds']>=30
            if not record['target_recovery'] or regression or record.get('business_window_failed'):
                raise RuntimeError('Sustained recovery or non-target gate failed')
        record['outcome_passed']=True
    except Exception as e:
        record.update(outcome_passed=False,error=str(e),traceback=traceback.format_exc())
        for name,args in [('pods',['get','pods','-o','json']),('deployments',['get','deployments','-o','json']),
                          ('events',['get','events','-o','json']),('payment',['logs','deployment/payment','--timestamps','--since=10m']),
                          ('rabbitmq-alarms',['exec','deployment/rabbitmq','--','rabbitmq-diagnostics','alarms'])]:
            try:
                p=subprocess.run(['kubectl','--context',rb.CONTEXT,'-n',rb.NS,*args],capture_output=True,text=True,timeout=30)
                (path/('failure-'+name+'.txt')).write_text(p.stdout+'\n'+p.stderr)
            except Exception as diagnostic_error:
                record.setdefault('diagnostic_errors',[]).append(str(diagnostic_error))
    finally:
        if thread and thread.is_alive():
            runner.cancel();thread.join(timeout=30)
        record['cleanup_started_at']=time.time()
        try:
            # Clean only after the outcome flag/observations have been saved.
            save(file,record)
            if key=='P1':rb.kubectl('delete','podchaos','pilot-pod-fail','--ignore-not-found','--timeout=60s')
            reset(path/'post-reset.log',protocol)
            record['post_reset_confirmed']=True
        except Exception as e:
            record['post_reset_confirmed']=False;record['reset_error']=str(e)
        record['cleanup_ended_at']=time.time();record['passed']=record.get('outcome_passed',False) and record.get('post_reset_confirmed',False)
        save(file,record)
    return record

def main():
    global OUT
    ap=argparse.ArgumentParser();ap.add_argument('--run-s2',action='store_true');ap.add_argument('--output',type=Path,default=OUT);a=ap.parse_args()
    OUT=a.output.resolve()
    if not a.run_s2:raise SystemExit('Specify --run-s2')
    protocol=json.loads((OUT/'protocol.json').read_text())
    rb.MANIFEST=ROOT/protocol['manifest'];rb.FINGERPRINT=rb.MANIFEST.with_suffix('.fingerprint.json')
    if sha(rb.MANIFEST)!=protocol['manifest_sha256']:raise SystemExit('Manifest differs from predeclared setup')
    for k,h in protocol['candidate_hashes'].items():
        if sha(ROOT/f'candidates/{k}.yaml')!=h:raise SystemExit('Candidate changed: '+k)
    results=[];confirmed_reset=None
    for i,case in enumerate(protocol['order']):
        path=OUT/f"{i+1:02d}-{case['candidate']}-{case['arm']}"
        if path.exists():raise SystemExit('Refusing to overwrite an existing arm: '+str(path))
        path.mkdir()
        print(f"START {i+1}/10 {case['candidate']} {case['arm']}",flush=True)
        record=run_arm(case['candidate'],case['arm'],path,protocol,confirmed_reset);results.append(record)
        confirmed_reset=path/'post-reset.log' if record.get('post_reset_confirmed') else None
        summary={'stage':'S2','passed':len(results)==10 and all(r['passed'] for r in results),
                 'completed_arms':len(results),'required_arms':10,'S4_trials':0,'main_trials':0,
                 'records':[{'candidate':r['candidate'],'arm':r['arm'],'passed':r['passed'],'error':r.get('error')} for r in results]}
        save(OUT/'S2-summary.json',summary)
        print(f"END {case['candidate']} {case['arm']} passed={record['passed']} {record.get('error','')}",flush=True)
        if not record['passed']:return 1
    return 0

if __name__=='__main__':sys.exit(main())

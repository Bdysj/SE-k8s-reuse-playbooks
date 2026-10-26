#!/usr/bin/env python3
"""S4 five registration repeats per candidate, gated on completed S3 feasibility."""
import argparse,hashlib,json,random,subprocess,sys,time
from pathlib import Path
import qualification as q
import reset_baseline as rb
import runner_control

ROOT=rb.ROOT
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')

def reset_s4(log,protocol):
    with log.open('w') as f:
        p=subprocess.run([sys.executable,'-u',str(ROOT/'tools/reset_baseline.py'),
            '--context',protocol['context'],'--manifest',str(ROOT/protocol['manifest']),
            '--tier-timeout','600'],stdout=f,stderr=subprocess.STDOUT,timeout=1500)
    if p.returncode:raise RuntimeError('Baseline reset failed: '+str(log))

def start_runner(key,path):
    (path/'runner').mkdir(parents=True,exist_ok=True)
    thread,runner=runner_control.run_async(private_data_dir=str(path/'runner'),project_dir=str(ROOT),
        playbook='candidates/'+key+'.yaml',inventory=str(ROOT/'tools/ansible-inventory.ini'),
        extravars={'kube_context':rb.CONTEXT},quiet=True,process_isolation=False,
        settings={'job_timeout':120,'idle_timeout':120},envvars={'ANSIBLE_HOST_KEY_CHECKING':'False'})
    runner.candidate_id=key
    return thread,runner

REQUIRED_ACTIONS={
 'P1':['Restart the pods to solve process crash process for deployment'],
 'P2':['Increase replica count by one'],
 'P3':['Set resource limits/requests','Set Quality of Service (QoS) classes','Set node affinity'],
 'P4':['Restore the consumed catalogue host on web','Wait for container recreation with the restored configuration'],
 'P5':['Restore only the declared web Service selector']}
original_execution_result=q.execution_result
def execution_s4(runner):
    result=original_execution_result(runner)
    reached={e['task'] for e in result['task_events'] if e['event']=='runner_on_ok'}
    result['necessary_actions']=REQUIRED_ACTIONS[runner.candidate_id]
    result['necessary_actions_not_successfully_reached']=sorted(set(result['necessary_actions'])-reached)
    result['execution_complete']=result['execution_complete'] and not result['necessary_actions_not_successfully_reached']
    return result

original_observe=q.observe
def observe_s4(key):
    o=original_observe(key)
    if 'rollout' not in o:o['rollout']=q.current_rollout(q.FAMILY[key])
    o['healthy']=o['healthy'] and o['rollout']['complete'] and o['rollout']['desired']==1
    return o

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prepare',action='store_true');ap.add_argument('--run',action='store_true')
    ap.add_argument('--s3-acceptance',type=Path,required=True);ap.add_argument('--output',type=Path,default=ROOT/'results/S4-registration-v1')
    a=ap.parse_args();out=a.output.resolve();s3=a.s3_acceptance.resolve()
    accepted=json.loads(s3.read_text())
    if not accepted.get('passed') or accepted.get('feasible')!=40:raise RuntimeError('S3 feasibility not complete')
    if a.prepare:
        out.mkdir(parents=True,exist_ok=True)
        if (out/'protocol.json').exists():raise RuntimeError('Do not overwrite a predeclared S4 protocol')
        p=json.loads((ROOT/'results/qualification-v3/protocol.json').read_text())
        p['version']='S4-registration-v1'
        p['purpose']='S4 registration qualification only; five new repeats per candidate, no S2/control/main trial counts'
        p['S3_acceptance']=str(s3.relative_to(ROOT));p['S3_acceptance_sha256']=sha(s3)
        order=[{'candidate':key,'repeat':i+1,'arm':'repair'} for key in p['candidate_hashes'] for i in range(5)]
        random.Random(20261003).shuffle(order);p['order']=order;p['order_random_seed']=20261003;p['written_at_epoch']=time.time()
        source=out/'tool-snapshot';source.mkdir()
        paths=[Path(__file__),ROOT/'tools/accept_registration.py',ROOT/'tools/runner_control.py',ROOT/'tools/qualification.py',ROOT/'tools/inject_fault.py',ROOT/'tools/reset_baseline.py']
        for t in paths:(source/t.name).write_bytes(t.read_bytes())
        p['tool_hashes']={t.name:sha(t) for t in paths}
        inventory=ROOT/'tools/ansible-inventory.ini'
        (source/inventory.name).write_bytes(inventory.read_bytes())
        p['inventory_sha256']=sha(inventory)
        p['source_archive']=str(source.relative_to(ROOT))
        p['source_notes']='S2 timing and HTTP predicates retained; owner-bound rollout checks strengthen all S4 target checks prospectively. Supported cancellation wrapper added; accepted S2 source snapshots preserved.'
        p['additional_S4_verification']='All targets also require current owner-bound ReplicaSet rollout, desired/ready replica count one, and zero target restarts. These are prospective stricter registration checks; historical S2 evidence is unchanged.'
        p['necessary_action_tasks']=REQUIRED_ACTIONS
        p['execution_gate']='Every listed necessary action must have runner_on_ok; skipped or absent necessary actions cannot qualify. All failed/unreachable task events remain execution errors.'
        p['registration']={'S2':'passed separately','S3':'passed separately','S4':'pending 25 repeats','main_trials':0}
        save(out/'protocol.json',p);print('Prepared 25 prospective S4 repeats.',flush=True);return 0
    if not a.run:raise RuntimeError('Choose --prepare or --run')
    p=json.loads((out/'protocol.json').read_text())
    if sha(s3)!=p['S3_acceptance_sha256']:raise RuntimeError('S3 evidence changed')
    if sha(ROOT/p['manifest'])!=p['manifest_sha256']:raise RuntimeError('Declared baseline changed')
    if sha(ROOT/'tools/ansible-inventory.ini')!=p['inventory_sha256']:raise RuntimeError('Fixed execution bindings changed')
    for n,h in p['tool_hashes'].items():
        if sha(ROOT/'tools'/n)!=h:raise RuntimeError('Tool changed: '+n)
    for k,h in p['candidate_hashes'].items():
        if sha(ROOT/f'candidates/{k}.yaml')!=h:raise RuntimeError('Candidate changed: '+k)
    rb.CONTEXT=p['context'];rb.NS=p['namespace']
    q.OUT=out;q.start_runner=start_runner;q.observe=observe_s4;q.execution_result=execution_s4;q.reset=reset_s4
    rb.MANIFEST=ROOT/p['manifest'];rb.FINGERPRINT=rb.MANIFEST.with_suffix('.fingerprint.json')
    records=[];prior=None
    for i,case in enumerate(p['order']):
        path=out/f"{i+1:02d}-{case['candidate']}-repeat-{case['repeat']}"
        if path.exists():raise RuntimeError('Refusing to overwrite '+str(path))
        path.mkdir();print(f"START {i+1}/25 {case['candidate']} repeat {case['repeat']}",flush=True)
        rec=q.run_arm(case['candidate'],'repair',path,p,prior)
        # The shared executor produces an S2-shaped raw record; archive it intact
        # and explicitly identify the registration trial in the outer record.
        (path/'executor-record.json').write_bytes((path/'record.json').read_bytes())
        rec.update(type='S4_registration_qualification_trial',repeat=case['repeat'],registered=False,S4_trials=1,main_trials=0)
        save(path/'record.json',rec);records.append(rec)
        prior=path/'post-reset.log' if rec.get('post_reset_confirmed') else None
        counts={k:sum(r['passed'] and r['candidate']==k for r in records) for k in p['candidate_hashes']}
        summary={'stage':'S4','completed_trials':len(records),'required_trials':25,'successful_per_candidate':counts,
                 'passed':len(records)==25 and all(n==5 for n in counts.values()),'registered_main_scenarios':0,'main_trials':0,
                 'records':[{'candidate':r['candidate'],'repeat':r['repeat'],'passed':r['passed'],'error':r.get('error')} for r in records]}
        save(out/'summary.json',summary)
        print(f"END {case['candidate']} repeat {case['repeat']} passed={rec['passed']} {rec.get('error','')}",flush=True)
        if not rec['passed']:return 1
    return 0

if __name__=='__main__':sys.exit(main())

#!/usr/bin/env python3
"""Resume only unqualified S4 slots, preserving prior successes and rejected evidence."""
import argparse,json,sys,time
from pathlib import Path
import qualification as q
import registration_trials as reg
import reset_baseline as rb
from audit_registration_evidence import ROOT,sha,read,audit_inputs,audit_trial

def save(path,data):path.write_text(json.dumps(data,indent=2)+'\n')
def accept(out,p):
    protocol=out/'protocol.json';audit_inputs(protocol)
    rows=[]
    prior=None
    for item in p['prior_successful_trials']:
        file=ROOT/item['record'];assert sha(file)==item['record_sha256']
        rows.append(audit_trial(file.parent,item,ROOT/p['prior_protocol'],prior))
        prior=file.parent/'post-reset.log'
    prior=None
    for i,case in enumerate(p['order']):
        path=out/f"{i+1:02d}-{case['candidate']}-repeat-{case['repeat']}"
        rows.append(audit_trial(path,case,protocol,prior));prior=path/'post-reset.log'
    identities={(r['candidate'],r['repeat']) for r in rows}
    assert len(identities)==len(rows)==25
    assert identities=={(key,i) for key in p['candidate_hashes'] for i in range(1,6)}
    for item in p['prior_rejected_trials']:
        assert sha(ROOT/item['record'])==item['record_sha256'] and not read(ROOT/item['record'])['passed']
    counts={key:sum(x['candidate']==key for x in rows) for key in p['candidate_hashes']}
    dest=out/'acceptance.json'
    if dest.exists():raise RuntimeError('Preserve existing acceptance')
    save(dest,{'stage':'S4','passed':True,'accepted_at':time.time(),'protocol':str(protocol.relative_to(ROOT)),
        'protocol_sha256':sha(protocol),'baseline':p['manifest'],'baseline_sha256':p['manifest_sha256'],
        'candidate_hashes':p['candidate_hashes'],'successful_qualification_trials':25,
        'qualification_attempts':p['prior_attempts']+len(p['order']),'successful_per_candidate':counts,
        'candidate_qualification_complete':True,'records':rows,'rejected_trials':p['prior_rejected_trials'],
        'S3_acceptance':p['S3_acceptance'],'registered_main_scenarios':0,'main_trials':0,
        'scope':'Baseline registration qualification only. Same images, five-user load, timing, necessary-task, current-owner, target/non-target and reset gates in both cohorts. Prior rejected evidence is retained; no changed-condition main outcome or current v3 replica fidelity is established.'})

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prepare',action='store_true');ap.add_argument('--run',action='store_true')
    ap.add_argument('--output',type=Path,default=ROOT/'results/S4-registration-v2');a=ap.parse_args();out=a.output.resolve()
    old=ROOT/'results/S4-registration-v1';oldp=old/'protocol.json'
    if a.prepare:
        if (out/'protocol.json').exists():raise RuntimeError('Preserve predeclared resume protocol')
        p=audit_inputs(oldp);summary=read(old/'summary.json')
        assert summary['completed_trials']==10 and not summary['passed']
        successes=[];rejected=[];prior=None
        for i,case in enumerate(p['order'][:10]):
            path=old/f"{i+1:02d}-{case['candidate']}-repeat-{case['repeat']}";file=path/'record.json';r=read(file)
            assert r['post_reset_confirmed']
            if r['passed']:
                proof=audit_trial(path,case,oldp,prior);successes.append(proof)
            else:
                assert i==9 and 'subprocess.TimeoutExpired' in r['traceback']
                rejected.append({'candidate':case['candidate'],'repeat':case['repeat'],'record':str(file.relative_to(ROOT)),
                    'record_sha256':sha(file),'reason':'20s shipping probe-exec collection timeout; recovery window incomplete. Qualification rejected, repair outcome indeterminate; concurrent host contention recorded but unique cause not established.'})
            prior=path/'post-reset.log'
        assert len(successes)==9 and len(rejected)==1
        p.update(version='S4-registration-v2-resume',purpose='Resume 16 unqualified slots after one collection rejection; preserve nine audited successes',
            prior_protocol=str(oldp.relative_to(ROOT)),prior_protocol_sha256=sha(oldp),prior_attempts=10,
            prior_successful_trials=successes,prior_rejected_trials=rejected,order=p['order'][9:],written_at_epoch=time.time())
        p['resume_policy']='Failed repeat and the fifteen unrun slots keep their original prospective order. First retry starts after a fresh confirmed seed reset. No time budget, candidate, image, demand or outcome gate is weakened. Record a new independent host window before running.'
        p['environment_window']='Pending user notification of closed resource-intensive applications; observed contention receipt in results/continuation-20261001/host-contention-S4-trial-10.json.'
        out.mkdir(parents=True,exist_ok=True);source=out/'tool-snapshot';source.mkdir()
        names=['resume_registration.py','audit_registration_evidence.py','registration_trials.py','runner_control.py','qualification.py','inject_fault.py','reset_baseline.py']
        for name in names:(source/name).write_bytes((ROOT/'tools'/name).read_bytes())
        (source/'ansible-inventory.ini').write_bytes((ROOT/'tools/ansible-inventory.ini').read_bytes())
        p['tool_hashes']={name:sha(ROOT/'tools'/name) for name in names};p['source_archive']=str(source.relative_to(ROOT))
        p['registration']={'S2':'passed separately','S3':'passed separately','S4':'nine successful, sixteen new trials pending','main_trials':0}
        save(out/'protocol.json',p);print('Prepared 16 S4 resume trials; nine successes audited; rejection retained.',flush=True);return 0
    if not a.run:raise RuntimeError('Choose --prepare or --run')
    p=audit_inputs(out/'protocol.json')
    for name,h in p['tool_hashes'].items():
        if sha(ROOT/'tools'/name)!=h:raise RuntimeError('Prospective source changed: '+name)
    assert sha(ROOT/p['S3_acceptance'])==p['S3_acceptance_sha256']
    assert sha(ROOT/p['prior_protocol'])==p['prior_protocol_sha256']
    window=read(out/'environment-window.json')
    if not window.get('confirmed') or window['recorded_at']<p['written_at_epoch']:raise RuntimeError('A fresh host window must be recorded before resuming')
    rb.CONTEXT=p['context'];rb.NS=p['namespace'];rb.MANIFEST=ROOT/p['manifest'];rb.FINGERPRINT=rb.MANIFEST.with_suffix('.fingerprint.json')
    q.OUT=out;q.start_runner=reg.start_runner;q.observe=reg.observe_s4;q.execution_result=reg.execution_s4;q.reset=reg.reset_s4
    records=[];prior=None
    for i,case in enumerate(p['order']):
        path=out/f"{i+1:02d}-{case['candidate']}-repeat-{case['repeat']}"
        if path.exists():raise RuntimeError('Preserve existing trial '+str(path))
        path.mkdir();print(f"START {i+1}/16 {case['candidate']} repeat {case['repeat']}",flush=True)
        r=q.run_arm(case['candidate'],'repair',path,p,prior)
        (path/'executor-record.json').write_bytes((path/'record.json').read_bytes())
        r.update(type='S4_registration_qualification_trial',repeat=case['repeat'],registered=False,S4_trials=1,main_trials=0)
        save(path/'record.json',r);records.append(r);prior=path/'post-reset.log' if r.get('post_reset_confirmed') else None
        counts={key:sum(x['candidate']==key for x in p['prior_successful_trials'])+sum(x['candidate']==key and x['passed'] for x in records) for key in p['candidate_hashes']}
        save(out/'summary.json',{'stage':'S4','completed_new_trials':len(records),'required_new_trials':16,
            'prior_successful_trials':9,'prior_rejected_trials':1,'qualification_attempts':10+len(records),
            'successful_per_candidate':counts,'passed':len(records)==16 and all(n==5 for n in counts.values()),
            'registered_main_scenarios':0,'main_trials':0,'records':[{'candidate':x['candidate'],'repeat':x['repeat'],'passed':x['passed'],'error':x.get('error')} for x in records]})
        print(f"END {case['candidate']} repeat {case['repeat']} passed={r['passed']} {r.get('error','')}",flush=True)
        if not r['passed']:return 1
    accept(out,p);print('S4 accepted: 25 successful qualifications; prior rejected attempt retained; main trials zero.',flush=True);return 0
if __name__=='__main__':sys.exit(main())

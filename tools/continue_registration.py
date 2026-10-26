#!/usr/bin/env python3
"""Preserve qualified/rejected cohorts and resume only the unqualified S4 slots."""
import argparse,copy,json,sys,time
from pathlib import Path
import qualification as q
import registration_trials as reg
import reset_baseline as rb
from audit_registration_evidence import ROOT,sha,read,audit_inputs,audit_trial

def save(path,data):path.write_text(json.dumps(data,indent=2)+'\n')
def audited_prior(item):
    file=ROOT/item['record'];assert sha(file)==item['record_sha256']
    path=file.parent;protocol=ROOT/item['protocol'];p=audit_inputs(protocol)
    assert sha(protocol)==item['protocol_sha256']
    case={'candidate':item['candidate'],'repeat':item['repeat']}
    receipt=path/'initial-reset-receipt.json';previous=None
    if receipt.exists():previous=ROOT/read(receipt)['source']
    return audit_trial(path,case,protocol,previous)

def accept(out,p):
    protocol=out/'protocol.json';audit_inputs(protocol)
    rows=[audited_prior(item) for item in p['prior_successful_trials']]
    previous=None
    for i,case in enumerate(p['order']):
        path=out/f"{i+1:02d}-{case['candidate']}-repeat-{case['repeat']}"
        rows.append(audit_trial(path,case,protocol,previous));previous=path/'post-reset.log'
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
      'scope':'Baseline registration qualification only. All cohorts retain identical application setup, candidate bindings, observation predicates, demands, timing, necessary-action/current-owner, non-target and reset gates. Every rejected attempt is retained and reported. No changed-condition repair outcome or current v3 replica fidelity is established.'})

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prepare',action='store_true');ap.add_argument('--run',action='store_true')
    ap.add_argument('--from-protocol',type=Path);ap.add_argument('--rejection-classification',type=Path)
    ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();out=a.output.resolve()
    if a.prepare:
        assert a.from_protocol and a.rejection_classification
        if (out/'protocol.json').exists():raise RuntimeError('Preserve existing protocol')
        previous_protocol=a.from_protocol.resolve();p=copy.deepcopy(audit_inputs(previous_protocol))
        prior_out=previous_protocol.parent;summary=read(prior_out/'summary.json')
        assert not summary['passed']
        for item in p['prior_successful_trials']:audited_prior(item)
        for item in p['prior_rejected_trials']:
            assert sha(ROOT/item['record'])==item['record_sha256'] and not read(ROOT/item['record'])['passed']
        classification_path=a.rejection_classification.resolve();classification=read(classification_path)
        successes=[];rejected=[];previous=None
        attempted=summary['completed_new_trials']
        for i,case in enumerate(p['order'][:attempted]):
            path=prior_out/f"{i+1:02d}-{case['candidate']}-repeat-{case['repeat']}";file=path/'record.json';r=read(file)
            assert r['post_reset_confirmed']
            if r['passed']:successes.append(audit_trial(path,case,previous_protocol,previous))
            else:
                assert i==attempted-1 and str(file.relative_to(ROOT))==classification['record']
                assert sha(file)==classification['record_sha256'] and not classification['qualification_passed']
                rejected.append({'candidate':case['candidate'],'repeat':case['repeat'],'record':str(file.relative_to(ROOT)),
                  'record_sha256':sha(file),'reason':classification['interpretation'],
                  'classification':str(classification_path.relative_to(ROOT)),'classification_sha256':sha(classification_path)})
            previous=path/'post-reset.log'
        assert len(rejected)==1
        remaining=p['order'][attempted-1:]
        p.update(version=out.name,purpose='Resume unqualified S4 slots; all successful and rejected cohorts retained',
          continued_from_protocol=str(previous_protocol.relative_to(ROOT)),continued_from_protocol_sha256=sha(previous_protocol),
          prior_attempts=p['prior_attempts']+attempted,prior_successful_trials=p['prior_successful_trials']+successes,
          prior_rejected_trials=p['prior_rejected_trials']+rejected,order=remaining,written_at_epoch=time.time())
        p['resume_policy']='Retry the rejected slot, then retain the original unrun order. First trial starts from a fresh complete seed restore. No candidate, application setup, demand, observation predicate or time budget changes. Stop and retain any further rejection. This cohort makes one prospective attempt per remaining slot.'
        p['environment_window']='Require a fresh measured host-window receipt following the no-fault network diagnostic. The diagnostic is not S4 or main evidence and does not change the original observer.'
        p['registration']={'S2':'passed separately','S3':'passed separately','S4':'incomplete; qualified and rejected attempts retained','main_trials':0}
        out.mkdir(parents=True,exist_ok=True);snapshot=out/'tool-snapshot';snapshot.mkdir()
        names=['continue_registration.py','audit_registration_evidence.py','registration_trials.py','runner_control.py','qualification.py','inject_fault.py','reset_baseline.py']
        for name in names:(snapshot/name).write_bytes((ROOT/'tools'/name).read_bytes())
        inventory=ROOT/'tools/ansible-inventory.ini';(snapshot/inventory.name).write_bytes(inventory.read_bytes())
        p['source_archive']=str(snapshot.relative_to(ROOT));p['tool_hashes']={name:sha(ROOT/'tools'/name) for name in names}
        save(out/'protocol.json',p);print(f'Prepared {len(remaining)} slots; {len(p["prior_successful_trials"])} valid successes and {len(p["prior_rejected_trials"])} rejections retained.',flush=True);return 0
    if not a.run:raise RuntimeError('Choose --prepare or --run')
    p=audit_inputs(out/'protocol.json')
    for name,h in p['tool_hashes'].items():
        if sha(ROOT/'tools'/name)!=h:raise RuntimeError('Prospective source changed: '+name)
    assert sha(ROOT/p['S3_acceptance'])==p['S3_acceptance_sha256']
    assert sha(ROOT/p['continued_from_protocol'])==p['continued_from_protocol_sha256']
    window=read(out/'environment-window.json')
    assert window.get('confirmed') and window['recorded_at']>=p['written_at_epoch']
    diagnostic=ROOT/window['diagnostic_record'];assert sha(diagnostic)==window['diagnostic_record_sha256']
    d=read(diagnostic);assert d['observed_seconds']>=600 and not d['errors']
    assert all(v['failures']==0 for v in d['per_variant'].values())
    rb.CONTEXT=p['context'];rb.NS=p['namespace'];rb.MANIFEST=ROOT/p['manifest'];rb.FINGERPRINT=rb.MANIFEST.with_suffix('.fingerprint.json')
    q.OUT=out;q.start_runner=reg.start_runner;q.observe=reg.observe_s4;q.execution_result=reg.execution_s4;q.reset=reg.reset_s4
    records=[];previous=None
    for i,case in enumerate(p['order']):
        path=out/f"{i+1:02d}-{case['candidate']}-repeat-{case['repeat']}"
        if path.exists():raise RuntimeError('Preserve existing trial '+str(path))
        path.mkdir();print(f"START {i+1}/{len(p['order'])} {case['candidate']} repeat {case['repeat']}",flush=True)
        r=q.run_arm(case['candidate'],'repair',path,p,previous)
        (path/'executor-record.json').write_bytes((path/'record.json').read_bytes())
        r.update(type='S4_registration_qualification_trial',repeat=case['repeat'],registered=False,S4_trials=1,main_trials=0)
        save(path/'record.json',r);records.append(r);previous=path/'post-reset.log' if r.get('post_reset_confirmed') else None
        counts={key:sum(x['candidate']==key for x in p['prior_successful_trials'])+sum(x['candidate']==key and x['passed'] for x in records) for key in p['candidate_hashes']}
        save(out/'summary.json',{'stage':'S4','completed_new_trials':len(records),'required_new_trials':len(p['order']),
          'prior_successful_trials':len(p['prior_successful_trials']),'prior_rejected_trials':len(p['prior_rejected_trials']),
          'qualification_attempts':p['prior_attempts']+len(records),'successful_per_candidate':counts,
          'passed':len(records)==len(p['order']) and all(n==5 for n in counts.values()),'registered_main_scenarios':0,'main_trials':0,
          'records':[{'candidate':x['candidate'],'repeat':x['repeat'],'passed':x['passed'],'error':x.get('error')} for x in records]})
        print(f"END {case['candidate']} repeat {case['repeat']} passed={r['passed']} {r.get('error','')}",flush=True)
        if not r['passed']:return 1
    accept(out,p);print('S4 accepted; all attempts retained; main trials zero.',flush=True);return 0
if __name__=='__main__':sys.exit(main())

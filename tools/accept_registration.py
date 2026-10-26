#!/usr/bin/env python3
"""Audit 25 independent S4 qualification records; never count them as main trials."""
import argparse,copy,hashlib,json,time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text())

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--results',type=Path,default=ROOT/'results/S4-registration-v1')
    out=ap.parse_args().results.resolve();p=read(out/'protocol.json');summary=read(out/'summary.json')
    dest=out/'acceptance.json'
    if dest.exists():raise RuntimeError('Preserve existing acceptance')
    assert summary['passed'] and summary['completed_trials']==25
    assert all(n==5 for n in summary['successful_per_candidate'].values())
    s3=ROOT/p['S3_acceptance'];assert sha(s3)==p['S3_acceptance_sha256']
    assert read(s3)['passed'] and read(s3)['feasible']==40
    assert sha(ROOT/p['manifest'])==p['manifest_sha256']
    assert sha((ROOT/p['manifest']).with_suffix('.fingerprint.json'))==p['fingerprint_sha256']
    assert sha(ROOT/'tools/ansible-inventory.ini')==p['inventory_sha256']
    for name,h in p['tool_hashes'].items():assert sha(out/'tool-snapshot'/name)==h
    for key,h in p['candidate_hashes'].items():assert sha(ROOT/f'candidates/{key}.yaml')==h
    rows=[];previous=None;seen=set()
    for i,case in enumerate(p['order']):
        path=out/f"{i+1:02d}-{case['candidate']}-repeat-{case['repeat']}"
        file=path/'record.json';r=read(file);raw=read(path/'executor-record.json')
        assert r['type']=='S4_registration_qualification_trial' and r['S4_trials']==1 and r['main_trials']==0
        assert r['candidate']==case['candidate'] and r['repeat']==case['repeat'] and r['arm']=='repair'
        assert r['started_at']>=p['written_at_epoch'] and r['protocol_sha256']==sha(out/'protocol.json')
        expected=copy.deepcopy(raw);expected.update(type=r['type'],repeat=case['repeat'],registered=False,S4_trials=1,main_trials=0)
        assert r==expected
        assert r['passed'] and r['outcome_passed'] and r['post_reset_confirmed']
        assert r['execution_complete'] and r['target_recovery'] and not r['non_target_regression']
        assert not r.get('error') and not r.get('reset_error') and not r.get('business_window_failed')
        e=r['execution'];assert e['status']=='successful' and e['rc']==0 and e['execution_complete']
        required=p['necessary_action_tasks'][case['candidate']]
        assert e['necessary_actions']==required and not e['necessary_actions_not_successfully_reached']
        reached={t['task'] for t in e['task_events'] if t['event']=='runner_on_ok'}
        assert set(required)<=reached
        assert not any(t['event'] in ('runner_on_failed','runner_on_unreachable') for t in e['task_events'])
        assert r['initial_non_target']['passed'] and r['non_target_before_execution']['passed']
        assert r['non_target_observations'] and all(n['passed'] for n in r['non_target_observations'])
        assert r['recovered_business'] and all(b['passed'] for b in r['recovered_business'])
        assert r['healthy_warmup'] and all(o['healthy'] and o['rollout']['complete'] and o['rollout']['desired']==1 for o in r['healthy_warmup'])
        assert r['fault_confirmation'] and not r['fault_confirmation'][-1]['healthy']
        samples=r['samples'];assert samples and samples[-1]['window_seconds']>=210 and r['window_observed_seconds']>=210
        assert r['repair_start_seconds']>=60
        assert all(not o['healthy'] for o in samples if o['window_seconds']<r['repair_start_seconds'])
        recovered=[o for o in samples if o['healthy']]
        assert recovered and recovered[0]['window_seconds']<=r['repair_start_seconds']+120
        assert r['sustained_recovery_seconds']>=30
        assert all(o['healthy'] and o['rollout']['complete'] and o['rollout']['desired']==1 for o in samples if o['window_seconds']>=recovered[0]['window_seconds'])
        assert r['cleanup_started_at']>=r['window_end_at'] and r['cleanup_ended_at']>=r['cleanup_started_at']
        if case['candidate']=='P1':
            assert r['finished_before_fault_expiry'] and r['window_end_at']<r['fault_expiry_expected_at']
        reset=path/'post-reset.log';assert 'baseline confirmed' in reset.read_text()
        if previous is None:assert 'baseline confirmed' in (path/'initial-reset.log').read_text()
        else:
            receipt=read(path/'initial-reset-receipt.json')
            assert receipt['boundary_reset_confirmed'] and receipt['source']==str(previous.relative_to(ROOT)) and receipt['sha256']==sha(previous)
        previous=reset
        ident=(case['candidate'],case['repeat']);assert ident not in seen;seen.add(ident)
        rows.append({'candidate':ident[0],'repeat':ident[1],'record':str(file.relative_to(ROOT)),
                     'record_sha256':sha(file),'sustained_recovery_seconds':r['sustained_recovery_seconds'],
                     'execution_complete':True,'target_recovery':True,'non_target_regression':False,'reset_confirmed':True})
    assert len(rows)==25 and all(sum(x['candidate']==k for x in rows)==5 for k in p['candidate_hashes'])
    result={'stage':'S4','passed':True,'accepted_at':time.time(),'protocol':str((out/'protocol.json').relative_to(ROOT)),
        'protocol_sha256':sha(out/'protocol.json'),'baseline':p['manifest'],'baseline_sha256':p['manifest_sha256'],
        'candidate_hashes':p['candidate_hashes'],'qualification_trials':25,'successful_per_candidate':summary['successful_per_candidate'],
        'candidate_qualification_complete':True,'S3_acceptance':p['S3_acceptance'],'records':rows,
        'registered_main_scenarios':0,'main_trials':0,
        'scope':'Five new baseline qualification repeats per frozen candidate. S3 constructions and S2 controls are separate. Current owner-bound rollout, necessary task events, sustained target recovery, named non-target checks and full reset are audited. Changed-condition reuse outcomes, comparative methods and RQ3b replica fidelity remain unevaluated.'}
    dest.write_text(json.dumps(result,indent=2)+'\n')
    print('S4 accepted: five successful qualification repeats for each frozen candidate; main trials zero.')
if __name__=='__main__':main()

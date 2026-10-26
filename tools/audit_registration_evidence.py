#!/usr/bin/env python3
"""Validate S4 evidence against each trial's own prospective protocol."""
import copy,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text())

def audit_inputs(protocol_path):
    p=read(protocol_path)
    assert sha(ROOT/p['manifest'])==p['manifest_sha256']
    assert sha((ROOT/p['manifest']).with_suffix('.fingerprint.json'))==p['fingerprint_sha256']
    assert sha(ROOT/'tools/ansible-inventory.ini')==p['inventory_sha256']
    for name,h in p['tool_hashes'].items():assert sha(protocol_path.parent/'tool-snapshot'/name)==h
    for key,h in p['candidate_hashes'].items():assert sha(ROOT/f'candidates/{key}.yaml')==h
    return p

def audit_trial(path,case,protocol_path,previous=None):
    p=read(protocol_path);file=path/'record.json';r=read(file);raw=read(path/'executor-record.json')
    assert r['type']=='S4_registration_qualification_trial' and r['S4_trials']==1 and r['main_trials']==0
    assert r['candidate']==case['candidate'] and r['repeat']==case['repeat'] and r['arm']=='repair'
    assert r['started_at']>=p['written_at_epoch'] and r['protocol_sha256']==sha(protocol_path)
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
    if case['candidate']=='P1':assert r['finished_before_fault_expiry'] and r['window_end_at']<r['fault_expiry_expected_at']
    reset=path/'post-reset.log';assert 'baseline confirmed' in reset.read_text()
    if previous is None:assert 'baseline confirmed' in (path/'initial-reset.log').read_text()
    else:
        receipt=read(path/'initial-reset-receipt.json')
        assert receipt['boundary_reset_confirmed'] and receipt['source']==str(previous.relative_to(ROOT)) and receipt['sha256']==sha(previous)
    return {'candidate':r['candidate'],'repeat':r['repeat'],'record':str(file.relative_to(ROOT)),
            'record_sha256':sha(file),'protocol':str(protocol_path.relative_to(ROOT)),
            'protocol_sha256':sha(protocol_path),'sustained_recovery_seconds':r['sustained_recovery_seconds'],
            'execution_complete':True,'target_recovery':True,'non_target_regression':False,'reset_confirmed':True}

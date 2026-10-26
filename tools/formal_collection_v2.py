"""Freeze and serially run bounded calibration or the registered RQ1 queue."""
import argparse
import copy
import fcntl
import hashlib
import json
import os
import random
import shutil
import time
from pathlib import Path
import yaml
import formal_replay_executor_v2 as executor
import formal_methods as methods
import main_executor_v4 as parent
import reset_baseline as rb
import audit_main_development as auditor
from prepare_reuse_flow_development import resolver_profile, RESOLVER_PROFILE, CONFIG

ROOT=rb.ROOT
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):executor.save(Path(path),value)

def freeze(out,p):
    snapshot=out/'tool-snapshot';snapshot.mkdir()
    paths=list((ROOT/'tools').glob('*.py'))+[ROOT/'tools/ansible-inventory.ini']
    for source in paths:(snapshot/source.name).write_bytes(source.read_bytes())
    p['tool_hashes']={x.name:sha(x) for x in paths}
    p['source_archive']=str(snapshot.relative_to(ROOT))
    p['protocol_path']=str((out/'protocol.json').relative_to(ROOT))
    save(out/'protocol.json',p)
    save(out/'freeze.json',{'at':time.time(),'protocol_sha256':sha(out/'protocol.json'),
                           'tool_hashes':p['tool_hashes'],'immutable':True})

def check(out):
    p=json.loads((out/'protocol.json').read_text());f=json.loads((out/'freeze.json').read_text())
    assert sha(out/'protocol.json')==f['protocol_sha256'],'Frozen protocol changed'
    for name,h in f['tool_hashes'].items():
        assert sha(ROOT/'tools'/name)==h,'Runtime source changed: '+name
        assert sha(out/'tool-snapshot'/name)==h,'Archived source changed: '+name
    for row in p['scenarios']+p['controls']:
        assert sha(ROOT/row['healthy_manifest'])==row['manifest_sha256']
    assert sha(ROOT/p['manifest'])==p['manifest_sha256']
    assert sha(ROOT/p['manifest_fingerprint'])==p['manifest_fingerprint_sha256']
    assert sha(ROOT/'candidates/registration.v1.json')==p['registration_sha256']
    if p.get('source_lock_sha256'):
        assert sha(ROOT/'sources/sources.lock.yaml')==p['source_lock_sha256'],'Pinned image/source lock changed'
    for key,h in p['candidate_hashes'].items():assert sha(ROOT/'candidates'/(key+'.yaml'))==h
    if p.get('comparison_model'):
        for name in ('comparison_model','whole_environment_references'):
            assert sha(ROOT/p[name])==p[name+'_sha256']
    p['protocol_sha256']=f['protocol_sha256']
    return p

def base_protocol(out):
    old=copy.deepcopy(parent.inputs(ROOT/'results/pre-main-development-v4'))
    out.mkdir(parents=True,exist_ok=True)
    if (out/'protocol.json').exists():raise RuntimeError('Preserve earlier protocol')
    accepted=ROOT/'results/reuse-flow-development-v8/final-acceptance.json'
    assert json.loads(accepted.read_text())['accepted']
    source=ROOT/'results/reuse-flow-development-v8/11-validation-timeout-P2/primary/baseline.yaml'
    baseline=out/'baseline.yaml';baseline.write_bytes(source.read_bytes())
    fingerprint=baseline.with_suffix('.fingerprint.json')
    rb.MANIFEST=baseline;save(fingerprint,rb.declared_fingerprint())
    profile=copy.deepcopy(RESOLVER_PROFILE);profile['evidence_sha256']=sha(ROOT/profile['evidence'])
    old.update(manifest=str(baseline.relative_to(ROOT)),manifest_sha256=sha(baseline),
               manifest_fingerprint=str(fingerprint.relative_to(ROOT)),manifest_fingerprint_sha256=sha(fingerprint),
               environment_profile=profile,decision_configuration=copy.deepcopy(CONFIG),
               verifier_sha256=sha(ROOT/'tools/main_verifier.py'),
               source_protocol='results/pre-main-development-v4/protocol.json',
               source_protocol_sha256=sha(ROOT/'results/pre-main-development-v4/protocol.json'),
               flow_acceptance=str(accepted.relative_to(ROOT)),flow_acceptance_sha256=sha(accepted),
               document_sha256=sha(ROOT/'D-DN-3.1_EN.docx'),
               current_scope='RQ1 independent counterfactual replays and saved initial RQ3a decisions; RQ3b not started')
    old['controls']=[r for r in old['controls'] if not r['id'].startswith('DEV-')]
    for row in old['scenarios']+old['controls']:
        docs=resolver_profile(list(yaml.safe_load_all((ROOT/row['healthy_manifest']).read_text())),True)
        path=out/(row['id']+'.healthy.yaml');path.write_text(yaml.safe_dump_all(docs,sort_keys=False,width=4096))
        row.update(healthy_manifest=str(path.relative_to(ROOT)),manifest_sha256=sha(path))
        row.pop('main_registered',None);row.pop('outcome_label',None)
    return old

def prepare_calibration(out):
    p=base_protocol(out)
    p.update(version='comparison-calibration-v3',stage='pre-main development supplementary registration',
             record_type='comparison_calibration_development',main_protocol_ready=False,
             registered_main_scenarios=0,main_trials=0,scenarios=[],
             comparison_model=None,whole_environment_references=None,
             purpose='Five original-condition successful-reference attempts under the accepted DNS profile. Preserve full namespace snapshots and actual outcomes; not formal main trials.')
    p['order']=[{'trial_id':f'calibration-{key}','scenario':key+'-original-conditions',
                 'arm':'repair','repeat':1,'category':'development'} for key in sorted(p['candidate_hashes'])]
    freeze(out,p)

def independent_audit(record,p,path):
    # Shared accepted trace predicates are applied to an explicit view; the raw
    # record is never rewritten or relabelled as development evidence.
    view=copy.deepcopy(record);view.update(type='pre-main_executor_development',main_trials=0,registered=False)
    result=auditor.trace_checks(view,p['timing'])
    for field in ('environment_after_initial_reset','environment_before_fault','environment_after_final_reset'):
        if not record.get(field,{}).get('passed'):result['failures'].append(field)
    for log in ('initial-reset.log','post-reset.log'):
        if not (path/log).exists() or 'baseline confirmed' not in (path/log).read_text():result['failures'].append(log)
    if record['protocol_sha256']!=p['protocol_sha256']:result['failures'].append('protocol identity')
    if record['candidate_sha256']!=p['candidate_hashes'][record['candidate']]:result['failures'].append('candidate identity')
    if record.get('trial_identity')!=p['trial_identity']:result['failures'].append('trial identity')
    if record['arm']=='repair' and record.get('action_executed'):
        boundary=record.get('execution_boundary_observation',{})
        if not record.get('boundary_common_safety_gate',{}).get('passed'):
            result['failures'].append('fresh execution-boundary safety gate')
        if not (0<=record['action_started_at']-boundary.get('started_at',0)<=p['decision_configuration']['observation_ttl']):
            result['failures'].append('fresh execution-boundary observation')
        changed=boundary.get('relevant_revision')!=record.get('pre_action_policy_observation',{}).get('relevant_revision')
        if changed and not record.get('boundary_reassessment'):result['failures'].append('changed revision reassessed')
        if record.get('pre_action_relevant_revision')!=boundary.get('relevant_revision'):
            result['failures'].append('revision identity at action')
        if record.get('initial_method_decisions',{}).get('at',float('inf'))>=record['action_started_at']:
            result['failures'].append('initial decisions before action')
    if p['trial_identity']['category']=='unrelated-change-control' and not record.get('unrelated_control_removed'):
        result['failures'].append('explicit unrelated-control cleanup')
    result.update(accepted=not result['failures'],raw_record_sha256=sha(path/'record.json'),
                  actual_record_type=record['type'],outcome=record['repair_outcome'],
                  scope='Same accepted independent trace checks; raw type and count retained.')
    return result

def run(out):
    p=check(out);runtime=out/'runtime';runtime.mkdir(exist_ok=True)
    lock=(runtime/'queue.lock').open('a+')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    save(runtime/'worker.json',{'pid':os.getpid(),'started_at':time.time(),'protocol_sha256':p['protocol_sha256']})
    rb.CONTEXT=p['context'];rb.NS=p['namespace'];rb.MANIFEST=ROOT/p['manifest'];rb.FINGERPRINT=ROOT/p['manifest_fingerprint']
    rows={x['id']:x for x in p['scenarios']+p['controls']}
    receipts=[]
    for i,entry in enumerate(p['order']):
        path=out/'trials'/entry['trial_id'];receipt=path/'audit.json'
        if receipt.exists():
            a=json.loads(receipt.read_text())
            if not a['accepted'] or sha(path/'record.json')!=a['raw_record_sha256']:
                raise RuntimeError('Rejected or changed prior trial: '+entry['trial_id'])
            receipts.append({'entry':entry,'audit':a});continue
        if path.exists():raise RuntimeError('Interrupted trial retained; recovery required before resuming: '+entry['trial_id'])
        if shutil.disk_usage(out).free < 4*2**30:raise RuntimeError('Less than 4GiB free; halt before new trial')
        check(out)
        category=entry['category'];case=copy.deepcopy(p)
        case['record_type']='formal_main_replay' if category=='main' else p['record_type']
        case['trial_identity']=entry
        save(runtime/'progress.json',{'state':'running','at':time.time(),'position':i+1,'total':len(p['order']),
                                     'current':entry,'completed':receipts})
        print(f"START {i+1}/{len(p['order'])} {entry['trial_id']}",flush=True)
        record=executor.run_case(rows[entry['scenario']],entry['arm'],path,case)
        a=independent_audit(record,case,path);save(receipt,a)
        receipts.append({'entry':entry,'audit':a})
        save(runtime/'progress.json',{'state':'running' if a['accepted'] else 'halted','at':time.time(),
                                     'position':i+1,'total':len(p['order']),'current':entry,'completed':receipts})
        print(f"END {entry['trial_id']}: {record['repair_outcome']}; trace accepted={a['accepted']}",flush=True)
        if not a['accepted']:raise RuntimeError('Preserved rejected trace; stop queue: '+str(a['failures']))
        if category=='development' and record['repair_outcome']!='Successful repair':
            raise RuntimeError('Supplementary registration failed; no snapshot certified')
    save(out/'collection-acceptance.json',{'at':time.time(),'accepted':True,'records':receipts,
                                         'protocol_sha256':p['protocol_sha256']})
    save(runtime/'progress.json',{'state':'complete','at':time.time(),'completed':receipts,'total':len(receipts)})

#!/usr/bin/env python3
"""Register five frozen candidates only after an audited complete S4 acceptance."""
import argparse,datetime,hashlib,json,time
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parent.parent
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text())
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--acceptance',type=Path,required=True)
    ap.add_argument('--output',type=Path,default=ROOT/'candidates/registration.v1.json');a=ap.parse_args()
    acceptance=a.acceptance.resolve();out=a.output.resolve()
    if out.exists():raise RuntimeError('Preserve existing registration')
    accepted=read(acceptance)
    assert accepted['passed'] and accepted['candidate_qualification_complete']
    assert accepted['successful_qualification_trials']==25
    assert accepted['successful_per_candidate']=={key:5 for key in ('P1','P2','P3','P4','P5')}
    protocol_path=ROOT/accepted['protocol'];p=read(protocol_path)
    assert sha(protocol_path)==accepted['protocol_sha256']
    assert sha(ROOT/p['manifest'])==p['manifest_sha256']
    assert sha((ROOT/p['manifest']).with_suffix('.fingerprint.json'))==p['fingerprint_sha256']
    assert sha(ROOT/'tools/ansible-inventory.ini')==p['inventory_sha256']
    s3_path=ROOT/p['S3_acceptance'];s3=read(s3_path)
    assert sha(s3_path)==p['S3_acceptance_sha256'] and s3['passed'] and s3['feasible']==40
    matrix_path=ROOT/'candidates/requirements.S3-feasible.v1.yaml'
    matrix=yaml.safe_load(matrix_path.read_text())
    assert matrix['status_counts']['feasibility_verified']==40
    registry=yaml.safe_load((ROOT/'candidates/registry.yaml').read_text())
    lock=yaml.safe_load((ROOT/'sources/sources.lock.yaml').read_text())
    source_pins={key:{field:value[field] for field in ('url','commit','license','helm_chart') if field in value}
        for key,value in lock['repositories'].items()}
    local_images={key:{field:value[field] for field in ('ref','image_id','architecture','built_from','deviation') if field in value}
        for key,value in lock['local_images']['builds'].items()}
    image_refs=[{'workload':obj['metadata']['name'],'container':c['name'],'image':c['image']}
        for obj in yaml.safe_load_all((ROOT/p['manifest']).read_text())
        if obj and obj.get('kind') in ('Deployment','StatefulSet')
        for c in obj['spec']['template']['spec']['containers']]
    rows=accepted['records'];assert len(rows)==25
    assert {(r['candidate'],r['repeat']) for r in rows}=={(key,i) for key in p['candidate_hashes'] for i in range(1,6)}
    for proof in rows:
        file=ROOT/proof['record'];r=read(file)
        assert sha(file)==proof['record_sha256'] and sha(ROOT/proof['protocol'])==proof['protocol_sha256']
        assert r['passed'] and r['execution_complete'] and r['target_recovery'] and not r['non_target_regression']
        assert r['post_reset_confirmed'] and r['main_trials']==0
        assert not r['execution']['necessary_actions_not_successfully_reached']
    candidates=[]
    for item in registry['candidates']:
        key=item['id'];file=ROOT/item['candidate']
        assert sha(file)==item['candidate_sha256']==p['candidate_hashes'][key]==matrix['candidate_hashes'][key]
        candidate={'id':key,'registered':True,'family':item['family'],'origin':item['origin'],
          'frozen_playbook':{'path':item['candidate'],'sha256':sha(file),'adaptations':item.get('adaptations',[]),
              'discovery_and_action_bindings':'The frozen file is authoritative; no locator or literal target is replaced to rescue a later replay.'},
          'historical_execution_evidence':{'successful_trials':5,'records':[r for r in rows if r['candidate']==key],
              'qualification_acceptance':str(acceptance.relative_to(ROOT)),'acceptance_sha256':sha(acceptance)},
          'four_condition_requirements':matrix['requirements'][key],
          'requirements_source':{'path':str(matrix_path.relative_to(ROOT)),'sha256':sha(matrix_path)},
          'necessary_actions':p['necessary_action_tasks'][key],
          'registered_fault':p['faults'][key],
          'scope':'Registered baseline repair only; S3 constructions establish feasibility, not successful changed-condition reuse.'}
        if item.get('source'):
            source=ROOT/item['source'];assert sha(source)==item['source_sha256']
            candidate['frozen_playbook']['upstream_source']={'path':item['source'],'sha256':sha(source)}
        else:candidate['frozen_playbook']['manual_repair_task']=item['repair_task']
        candidates.append(candidate)
    now=time.time();record={'version':'playbook-registration-v1','registered_at':now,
      'registered_date':datetime.datetime.now().astimezone().isoformat(),'registered_candidates':5,'candidates':candidates,
      'declared_setup':{'manifest':p['manifest'],'manifest_sha256':p['manifest_sha256'],
          'fingerprint_sha256':p['fingerprint_sha256'],'context':p['context'],'namespace':p['namespace'],
          'inventory':'tools/ansible-inventory.ini','inventory_sha256':p['inventory_sha256'],
          'qualification_protocol':str(protocol_path.relative_to(ROOT)),'qualification_protocol_sha256':sha(protocol_path),
          'source_pins':source_pins,'application_image_refs':image_refs,'local_immutable_image_records':local_images,
          'workload_random_seed':p['load_python_random_seed'],'placement':p['placement'],
          'gateway_substitute':p['payment_gateway'],
          'verification':{key:p[key] for key in ('timing_seconds','target_check','non_target_checks','additional_S4_verification','execution_gate')}},
      'qualification_successes':25,'qualification_attempts':accepted['qualification_attempts'],
      'rejected_attempts':accepted['rejected_trials'],'S3_feasible_constructions':40,
      'registered_main_scenarios':0,'main_replay_trials':0,
      'main_protocol_ready':False,
      'limitations':['Historical S4 non-target gate checks the full window conservatively. Main Section 3.6 labels require synchronous immediately-pre-action checks and separate post-action failures.',
          'Scenario-specific current verifiers, same-fault matched controls, prototype rules, permitted observations, evidence policy and comparison settings must be frozen before main collection.',
          'Current v3 validation-replica fidelity is not established by the earlier baseline.arm64 dual-cluster pilot.']}
    out.write_text(json.dumps(record,indent=2)+'\n')
    print(f'Registered five unchanged candidates after {record["qualification_attempts"]} recorded attempts; main trials zero.')
    return 0
if __name__=='__main__':raise SystemExit(main())

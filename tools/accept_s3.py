#!/usr/bin/env python3
"""Audit complete prospective S3 evidence; never execute repairs or register main trials."""
import argparse,hashlib,json,subprocess,sys,time
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parent.parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--results',type=Path,default=ROOT/'results/S3-feasibility-v1');a=ap.parse_args();out=a.results.resolve()
    p=json.loads((out/'protocol.json').read_text());summary=json.loads((out/'summary.json').read_text())
    if summary['completed']!=40:raise RuntimeError('S3 batch not complete')
    if (out/'acceptance.json').exists():raise RuntimeError('Acceptance already exists; preserve it')
    req=ROOT/'candidates/requirements.v1.yaml'
    assert sha(req)==p['requirements_sha256']
    assert sha(ROOT/p['base_manifest'])==p['base_manifest_sha256']
    for n,h in p['tool_hashes'].items():assert sha(out/'tool-snapshot'/n)==h
    for k,h in p['candidate_hashes'].items():assert sha(ROOT/f'candidates/{k}.yaml')==h
    replacement=ROOT/'results/S3-mysql-replacement-v2'
    replacement_summary=json.loads((replacement/'summary.json').read_text());assert replacement_summary['passed']
    replacement_protocol=json.loads((replacement/'protocol.json').read_text())
    sql_gate=json.loads((replacement/'P2-3-1/actual-sql-readiness.json').read_text())
    assert sql_gate and sql_gate[-1]['passed']
    endpoint=ROOT/'results/S3-endpoint-replacement-v3'
    endpoint_summary=json.loads((endpoint/'summary.json').read_text());assert endpoint_summary['passed']
    endpoint_protocol=json.loads((endpoint/'protocol.json').read_text())
    rollout=ROOT/'results/S3-rollout-constraint-v2'
    rollout_summary=json.loads((rollout/'summary.json').read_text());assert rollout_summary['passed']
    rollout_protocol=json.loads((rollout/'protocol.json').read_text())
    assert sha(ROOT/rollout_protocol['requirements'])==rollout_protocol['requirements_sha256']
    state=json.loads((rollout/'P3-2-2/pre-reset-state.json').read_text())
    dry=state['candidate_final_patch_dry_run'];assert dry['rc']==0
    assert dry['body']['spec']['template']['spec']['affinity']['podAntiAffinity']['requiredDuringSchedulingIgnoredDuringExecution']
    web=next(d for d in state['deployments']['items'] if d['metadata']['name']=='web')
    assert web['spec']['strategy']['rollingUpdate']=={'maxSurge':1,'maxUnavailable':0}
    assert web['spec']['template']['spec']['affinity']['podAntiAffinity']['requiredDuringSchedulingIgnoredDuringExecution']
    assert any(e.get('reason')=='FailedScheduling' and 'anti-affinity' in e.get('message','') for e in state['events']['items'])
    supplement=ROOT/'results/S3-relocation-integrity-v1'
    supplemental_summary=json.loads((supplement/'summary.json').read_text())
    assert supplemental_summary['passed']
    supplemental_protocol=json.loads((supplement/'protocol.json').read_text())
    temporal=ROOT/'results/S3-observation-window-v2'
    # Complete the separately predeclared temporal supplement sequentially after
    # the other construction checks, before accepting any S3 evidence.
    if not (temporal/'summary.json').exists():
        if not (temporal/'protocol.json').exists():raise RuntimeError('Temporal supplement must be predeclared')
        print('START predeclared temporal supplement P3-3-1',flush=True)
        with (temporal/'run.log').open('w') as f:
            child=subprocess.run([sys.executable,'-u',str(ROOT/'tools/check_observation_window.py'),'--run'],stdout=f,stderr=subprocess.STDOUT)
        if child.returncode:raise RuntimeError('Temporal supplement rejected; preserve its evidence')
    temporal_summary=json.loads((temporal/'summary.json').read_text());assert temporal_summary['passed']
    temporal_protocol=json.loads((temporal/'protocol.json').read_text())
    for folder,protocol in [(replacement,replacement_protocol),(endpoint,endpoint_protocol),(rollout,rollout_protocol),(supplement,supplemental_protocol),(temporal,temporal_protocol)]:
        assert protocol['base_manifest_sha256']==p['base_manifest_sha256']
        assert protocol['candidate_hashes']==p['candidate_hashes']
        for name,h in protocol['tool_hashes'].items():assert sha(folder/'tool-snapshot'/name)==h
    extra=json.loads((ROOT/supplemental_summary['record']).read_text())
    assert extra['passed'] and extra['reset_confirmed'] and not extra['candidate_executed']
    assert extra['started_at']>=supplemental_protocol['written_at']
    assert extra['construction']['cart_check'] and extra['construction']['vote_check']
    assert all(h['passed'] and h['target']['cart']['passed'] and h['target']['vote']['passed'] for h in extra['healthy_window'])
    assert extra['fault_observations'][-1]['window_seconds']>=30
    assert all(not t['healthy'] for t in extra['fault_observations'])
    assert all(n['passed'] for n in extra['non_target_observations'])
    pairs=[];seen=set()
    for i,row in enumerate(p['scenarios']):
        initial_path=out/f"{i+1:02d}-{row['id']}"/'record.json'
        if row['id']=='P3-2-2':row=rollout_protocol['scenario']
        path=ROOT/replacement_summary['record'] if row['id']=='P2-3-1' else ROOT/endpoint_summary['record'] if row['id']=='P5-3-1' else ROOT/rollout_summary['record'] if row['id']=='P3-2-2' else ROOT/temporal_summary['record'] if row['id']=='P3-3-1' else initial_path
        r=json.loads(path.read_text())
        if row['id']=='P2-3-1':
            assert r['started_at']>=replacement_protocol['written_at']
            assert not json.loads(initial_path.read_text())['passed']
        if row['id']=='P5-3-1':
            assert r['started_at']>=endpoint_protocol['written_at']
            assert not json.loads(initial_path.read_text())['passed']
        if row['id']=='P3-2-2':
            assert r['started_at']>=rollout_protocol['written_at']
            assert not json.loads(initial_path.read_text())['passed']
        if row['id']=='P3-3-1':
            assert r['started_at']>=temporal_protocol['written_at']
            original=json.loads(initial_path.read_text())
            assert original['passed'] and original['fault_observations'][-1]['window_seconds']<30
        assert r['scenario']==row['id'] and r['construction']==row['construction']
        assert r['passed'] and r['construction_passed'] and r['reset_confirmed']
        assert r['candidate_executed'] is False and r['registered'] is False and r['main_trials']==0 and r['S4_trials']==0
        assert r['started_at']>=p['written_at']
        assert r['healthy_setup_checks'][-1]['passed'] and r['healthy_window'] and all(h['passed'] for h in r['healthy_window'])
        assert all(not t['healthy'] for t in r['fault_observations'])
        assert r['fault_observations'][-1]['window_seconds']>=30
        assert all(n['passed'] for n in r['non_target_observations'])
        if row['id']=='P3-4-1':
            for h in r['healthy_window']:
                current={x['uid'] for x in h['target']['rollout']['pods']}
                assert all(ep['targetRef']['uid'] in current for ep in h['target']['endpoint_owner']['ready_endpoints'])
        if row['id']=='P5-3-1':
            for h in r['healthy_window']:
                assert all(ep['targetRef']['uid']==r['replacement']['new_uid'] for ep in h['target']['endpoint_owner']['ready_endpoints'])
        assert sha(ROOT/row['healthy_manifest'])==row['manifest_sha256']
        assert 'baseline confirmed' in (path.parent/'post-reset.log').read_text()
        if row['candidate']=='P1':assert r['finished_before_expiry']
        if row['construction']['remove_node_label']:
            assert r['node_labels_restored']
            assert all(all(v is None for v in h['additional_fields']['node_legacy_labels'].values()) for h in r['healthy_window'])
        assert row['id'] not in seen;seen.add(row['id'])
        pairs.append({'scenario':row['id'],'candidate':row['candidate'],'condition':row['condition'],
                      'feasible':True,'record':str(path.relative_to(ROOT)),'record_sha256':sha(path),
                      'last_fault_observation_seconds':r['fault_observations'][-1]['window_seconds'],
                      'construction':row['construction'],'candidate_executed':False,'registered_main_scenario':False})
        if row['id']=='P4-2-2':
            pairs[-1]['supplemental_consumer_record']=supplemental_summary['record']
            pairs[-1]['supplemental_record_sha256']=sha(ROOT/supplemental_summary['record'])
        if row['id']=='P3-3-1':
            pairs[-1]['supplemented_temporal_record']=str(initial_path.relative_to(ROOT))
            pairs[-1]['temporal_protocol']=str((temporal/'protocol.json').relative_to(ROOT))
        if row['id'] in ('P2-3-1','P5-3-1','P3-2-2'):
            pairs[-1]['previous_rejected_record']=str(initial_path.relative_to(ROOT))
            pairs[-1]['corrected_setup_protocol']=str(((replacement if row['id']=='P2-3-1' else endpoint if row['id']=='P5-3-1' else rollout)/'protocol.json').relative_to(ROOT))
    assert len(seen)==40
    conditions=['target_objects','referenced_fields','resource_relationships','verification_criteria']
    assert all(sum(r['candidate']==k for r in pairs)==8 for k in p['candidate_hashes'])
    assert all(sum(r['condition']==c for r in pairs)==10 for c in conditions)
    qualified=yaml.safe_load((ROOT/rollout_protocol['requirements']).read_text());qualified.update(version='requirements-S3-feasible-v1',stage='S3 passed; S4 qualification/main registration pending',derived_from=rollout_protocol['requirements'])
    qualified['feasibility_accepted_at_epoch']=time.time()
    qualified['status_counts'].update(feasibility_verified=40)
    for row in qualified['proposals']:
        proof=next(x for x in pairs if x['scenario']==row['id']);cfg=proof['construction']
        row.update(feasibility_verified=True,feasibility_record=proof['record'],feasibility_record_sha256=proof['record_sha256'],
                   construction=cfg,overlapping_conditions=[c for c in cfg['overlapping_conditions'] if c in conditions],
                   additional_construction_notes=[c for c in cfg['overlapping_conditions'] if c not in conditions],
                   main_registered=False)
        if row['id']=='P4-2-2':
            row['supplemental_consumer_record']=supplemental_summary['record']
            row['construction'].update(cart_check=True,vote_check=True)
    qualified_path=ROOT/'candidates/requirements.S3-feasible.v1.yaml'
    if qualified_path.exists():raise RuntimeError('Preserve existing qualified matrix')
    qualified_path.write_text(yaml.safe_dump(qualified,sort_keys=False,width=110))
    result={'stage':'S3','passed':True,'completed':40,'feasible':40,'accepted_at':time.time(),
            'protocol':str((out/'protocol.json').relative_to(ROOT)),'protocol_sha256':sha(out/'protocol.json'),
            'requirements':str(qualified_path.relative_to(ROOT)),'requirements_sha256':sha(qualified_path),
            'audit_source_sha256':sha(Path(__file__)),
            'pairs':pairs,'supplemental_consumer_check':str(supplement.relative_to(ROOT)),
            'corrected_mysql_replacement':str(replacement.relative_to(ROOT)),
            'corrected_endpoint_observer':str(endpoint.relative_to(ROOT)),
            'replacement_infeasible_proposal':str(rollout.relative_to(ROOT)),
            'temporal_supplement':str(temporal.relative_to(ROOT)),
            'development_construction_trials':45,'rejected_harness_attempts':2,'rejected_infeasible_proposals':1,
            'successful_construction_trials':42,
            'candidate_executions':0,'S4_trials':0,'registered_main_scenarios':0,'main_trials':0,
            'limits':'Construction feasibility only: 15s healthy window and >=30s observed symptom, full reset each case. No frozen candidate executed, no observed reuse labels. Original expectations remain hypotheses; variable/multiplicity overlaps are explicit.'}
    (out/'acceptance.json').write_text(json.dumps(result,indent=2)+'\n')
    print('S3 accepted: 40/40 constructions, 20 requirements; S4/main counts zero.')
if __name__=='__main__':main()

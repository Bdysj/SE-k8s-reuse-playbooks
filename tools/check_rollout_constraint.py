#!/usr/bin/env python3
"""Prospective replacement of an infeasible managed-node-label S3 proposal."""
import argparse,copy,json,subprocess,time
from pathlib import Path
import yaml
import scenario_feasibility as s
import reset_baseline as rb

ROOT=rb.ROOT
out=ROOT/'results/S3-rollout-constraint-v2'
original_reset=s.reset
def reset_with_snapshot(log):
    if log.name=='post-reset.log':
        state={k:rb.kjson('get',k) for k in ['deployments','pods','events']}
        rb_patch={'spec':{'template':{'metadata':{'annotations':{'scheduler.alpha.kubernetes.io/qosClass':'burstable'}},'spec':{
          'containers':[{'name':'web','resources':{'requests':{'cpu':'100m','memory':'256Mi'},'limits':{'cpu':'200m','memory':'512Mi'}}}],
          'affinity':{'nodeAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':{'nodeSelectorTerms':[{'matchExpressions':[{'key':'beta.kubernetes.io/os','operator':'In','values':['linux']}]}]}}}}}}}
        p=subprocess.run(['kubectl','--context',rb.CONTEXT,'-n',rb.NS,'patch','deployment','web','--type=strategic','-p',json.dumps(rb_patch),'--dry-run=server','-o','json'],capture_output=True,text=True,timeout=30)
        state['candidate_final_patch_dry_run']={'rc':p.returncode,'body':json.loads(p.stdout) if p.returncode==0 else p.stderr,
            'scope':'Server-side admission query only; no actual repair action or candidate execution.'}
        s.save(log.parent/'pre-reset-state.json',state)
    original_reset(log)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prepare',action='store_true');ap.add_argument('--run',action='store_true');a=ap.parse_args()
    if a.prepare:
        out.mkdir(parents=True,exist_ok=True)
        if (out/'protocol.json').exists():raise RuntimeError('Preserve replacement expectations')
        p=json.loads((ROOT/'results/S3-feasibility-v1/protocol.json').read_text())
        row=copy.deepcopy(next(x for x in p['scenarios'] if x['id']=='P3-2-2'))
        ds=s.docs();d=s.obj(ds,'Deployment','web')
        d['spec']['strategy']={'type':'RollingUpdate','rollingUpdate':{'maxSurge':1,'maxUnavailable':0}}
        d['spec']['template']['spec']['affinity']={'podAntiAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':[
          {'labelSelector':{'matchLabels':{'service':'web'}},'topologyKey':'kubernetes.io/hostname'}]}}
        manifest=out/'P3-2-2.healthy.yaml';manifest.write_text(yaml.safe_dump_all(ds,sort_keys=False,width=4096))
        cfg=row['construction'];cfg.update(remove_node_label=False,delete_deployments_before=['web'],overlapping_conditions=['target_objects','resource_relationships','controller_rollout_constraint'])
        row.update(healthy_manifest=str(manifest.relative_to(ROOT)),manifest_sha256=s.sha(manifest),
          change='Recreate same-named web with one healthy Pod, hostname placement, hard self anti-affinity, maxSurge=1/maxUnavailable=0. Subsequent resource-template update cannot schedule a surge Pod beside the existing Pod. Frozen P3 retains that anti-affinity; current-template recovery requires new evidence.',
          replaces_infeasible='results/S3-feasibility-v1/29-P3-2-2/record.json')
        req=yaml.safe_load((ROOT/'candidates/requirements.v1.yaml').read_text())
        text='Fixed resources and retained affinity/rollout fields must permit a ready current template on the declared node. beta.kubernetes.io/os=linux alone does not establish schedulability. Legacy annotation alone does not establish actual QoS.'
        req.update(version='requirements-S3-revised-v2',stage='One infeasible pre-freeze proposal replaced; feasibility pending',derived_from='candidates/requirements.v1.yaml')
        req['requirements']['P3']['referenced_fields']=text
        for r in req['proposals']:
            if r['candidate']=='P3' and r['condition']=='referenced_fields':r['requirement']=text
            if r['id']=='P3-2-2':r.update(change=row['change'],replaces_infeasible=row['replaces_infeasible'])
        row['requirement']=text
        rp=ROOT/'candidates/requirements.S3-revised.v2.yaml'
        if rp.exists():raise RuntimeError('Preserve revised requirement matrix')
        rp.write_text(yaml.safe_dump(req,sort_keys=False,width=110))
        source=out/'tool-snapshot';source.mkdir()
        ts=[Path(__file__),ROOT/'tools/scenario_feasibility.py',ROOT/'tools/qualification.py',ROOT/'tools/reset_baseline.py',ROOT/'tools/inject_fault.py']
        for t in ts:(source/t.name).write_bytes(t.read_bytes())
        s.save(out/'protocol.json',{'stage':'S3 pre-freeze infeasible-proposal replacement','written_at':time.time(),'scenario':row,
          'base_manifest_sha256':s.sha(s.BASE),'candidate_hashes':p['candidate_hashes'],'tool_hashes':{t.name:s.sha(t) for t in ts},
          'requirements':str(rp.relative_to(ROOT)),'requirements_sha256':s.sha(rp),
          'reason':'All fourteen healthy setup reads over 90s reported the removed legacy node label as linux. The label-removal proposal was not stable in this lab. Replace before main freeze, preserve rejection, do not enforce repeated label removal or change policies.',
          'gate':'Healthy single web Pod with hard anti-affinity and ordinary unchanged business graph; same 1Mi fault creates unready desired rollout; named non-target checks and full reset pass. Capture scheduler events and nonmutating server-side final-patch admission.',
          'constants':'Exact images, five-user demand, physical three-node/8CPU capacity and security policies unchanged. Deployment UID is refreshed during construction; placement and rollout constraints overlap resource relationships.',
          'expectation_is_not_outcome_label':True,'candidate_executions':0,'S4_trials':0,'main_trials':0})
        print('Prepared replacement field scenario and revised requirement clause.',flush=True);return
    if not a.run:raise RuntimeError('Choose --prepare or --run')
    p=json.loads((out/'protocol.json').read_text())
    for n,h in p['tool_hashes'].items():
        if s.sha(ROOT/'tools'/n)!=h:raise RuntimeError('Prospective source changed')
    for k,h in p['candidate_hashes'].items():
        if s.sha(ROOT/f'candidates/{k}.yaml')!=h:raise RuntimeError('Candidate changed')
    if s.sha(s.BASE)!=p['base_manifest_sha256'] or s.sha(ROOT/p['requirements'])!=p['requirements_sha256']:raise RuntimeError('Input changed')
    rb.MANIFEST=s.BASE;rb.FINGERPRINT=s.BASE.with_suffix('.fingerprint.json');s.reset=reset_with_snapshot
    path=out/'P3-2-2'
    if path.exists():raise RuntimeError('Preserve existing replacement trial')
    path.mkdir();r=s.run_case(p['scenario'],path,None)
    s.save(out/'summary.json',{'passed':r['passed'],'scenario':'P3-2-2','record':str((path/'record.json').relative_to(ROOT)),
                            'candidate_executions':0,'S4_trials':0,'main_trials':0,'error':r.get('error')})
    print('Replacement rollout constraint:',r['passed'],r.get('error',''),flush=True)
    if not r['passed']:raise SystemExit(1)
if __name__=='__main__':main()

#!/usr/bin/env python3
"""Repeat S3 Pod replacement after nil-safe EndpointSlice observation fix."""
import argparse,copy,json,time
from pathlib import Path
import scenario_feasibility as s
import reset_baseline as rb

ROOT=rb.ROOT
def endpoints(service,workload):
    current=s.q.current_rollout(workload)
    owned={p['uid'] for p in current['pods']}
    slices=rb.kjson('get','endpointslices','-l','kubernetes.io/service-name='+service)['items']
    eps=[ep for item in slices for ep in (item.get('endpoints') or []) if ep.get('conditions',{}).get('ready')]
    return {'service':service,'deployment':workload,'owned_pod_uids':sorted(owned),'ready_endpoints':eps,
            'current_rollout':current,'passed':current['complete'] and bool(eps) and all(ep.get('targetRef',{}).get('uid') in owned for ep in eps)}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prepare',action='store_true');ap.add_argument('--run',action='store_true');a=ap.parse_args()
    out=ROOT/'results/S3-endpoint-replacement-v3'
    if a.prepare:
        out.mkdir(parents=True,exist_ok=True)
        if (out/'protocol.json').exists():raise RuntimeError('Preserve existing endpoint plan')
        p=json.loads((ROOT/'results/S3-feasibility-v1/protocol.json').read_text())
        row=copy.deepcopy(next(x for x in p['scenarios'] if x['id']=='P5-3-1'))
        source=out/'tool-snapshot';source.mkdir()
        ts=[Path(__file__),ROOT/'tools/scenario_feasibility.py',ROOT/'tools/qualification.py',ROOT/'tools/reset_baseline.py',ROOT/'tools/inject_fault.py']
        for t in ts:(source/t.name).write_bytes(t.read_bytes())
        s.save(out/'protocol.json',{'stage':'S3 corrected EndpointSlice observer repeat','written_at':time.time(),'scenario':row,
          'base_manifest_sha256':s.sha(s.BASE),'candidate_hashes':p['candidate_hashes'],'tool_hashes':{t.name:s.sha(t) for t in ts},
          'previous_rejected_record':'results/S3-feasibility-v1/28-P5-3-1/record.json',
          'reason':'After the wrong-selector fault, v1 iterated endpoints:null instead of reporting zero ready endpoints. The frozen candidate never executed. Prior unexecuted v2 preparation had an incorrect numeric reference; preserve it.',
          'correction':'Interpret null endpoints as empty, retain existing 90s healthy setup budget; accept only ready EndpointSlice targetRefs belonging to current owner-bound rollout Pods.',
          'candidate_executions':0,'S4_trials':0,'main_trials':0})
        print('Prepared nil-safe current-Pod endpoint repeat.',flush=True);return
    if not a.run:raise RuntimeError('Choose --prepare or --run')
    p=json.loads((out/'protocol.json').read_text())
    for n,h in p['tool_hashes'].items():
        if s.sha(ROOT/'tools'/n)!=h:raise RuntimeError('Prospective tool changed')
    for k,h in p['candidate_hashes'].items():
        if s.sha(ROOT/f'candidates/{k}.yaml')!=h:raise RuntimeError('Candidate changed')
    if s.sha(s.BASE)!=p['base_manifest_sha256']:raise RuntimeError('Base changed')
    rb.MANIFEST=s.BASE;rb.FINGERPRINT=s.BASE.with_suffix('.fingerprint.json');s.endpoints=endpoints
    path=out/'P5-3-1'
    if path.exists():raise RuntimeError('Preserve existing endpoint repeat')
    path.mkdir();r=s.run_case(p['scenario'],path,None)
    s.save(out/'summary.json',{'passed':r['passed'],'scenario':'P5-3-1','record':str((path/'record.json').relative_to(ROOT)),
                            'candidate_executions':0,'S4_trials':0,'main_trials':0,'error':r.get('error')})
    print('Corrected endpoints:',r['passed'],r.get('error',''),flush=True)
    if not r['passed']:raise SystemExit(1)
if __name__=='__main__':main()

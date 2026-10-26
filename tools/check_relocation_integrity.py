#!/usr/bin/env python3
"""Supplement S3 relocation with explicit checks of every catalogue consumer."""
import argparse,copy,json,time
from pathlib import Path
import scenario_feasibility as s
import reset_baseline as rb

ROOT=rb.ROOT
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prepare',action='store_true');ap.add_argument('--run',action='store_true');a=ap.parse_args()
    out=ROOT/'results/S3-relocation-integrity-v1'
    if a.prepare:
        out.mkdir(parents=True,exist_ok=True)
        if (out/'protocol.json').exists():raise RuntimeError('Do not overwrite prospective consumer check')
        p=json.loads((ROOT/'results/S3-feasibility-v1/protocol.json').read_text())
        row=copy.deepcopy(next(x for x in p['scenarios'] if x['id']=='P4-2-2'))
        row['construction'].update(cart_check=True,vote_check=True)
        source=out/'tool-snapshot';source.mkdir()
        ts=[Path(__file__),ROOT/'tools/scenario_feasibility.py',ROOT/'tools/qualification.py',ROOT/'tools/reset_baseline.py',ROOT/'tools/inject_fault.py']
        for t in ts:(source/t.name).write_bytes(t.read_bytes())
        s.save(out/'protocol.json',{'stage':'S3 supplementary consumer integrity','written_at':time.time(),'scenario':row,
          'base_manifest_sha256':s.sha(s.BASE),'candidate_hashes':p['candidate_hashes'],'tool_hashes':{t.name:s.sha(t) for t in ts},
          'reason':'Existing v1 relocation gate checked all six read/health paths and declared consumer configuration; add explicit cart and ratings writes to establish operational consumption of new Service by all three clients.',
          'gate':'Healthy current frontend catalogue read, isolated-cart add/read via cart, accepted vote + fetch/SQL count via ratings, alongside original healthy/fault/reset gates.',
          'vote_policy':p['vote_policy'],'candidate_executions':0,'S4_trials':0,'main_trials':0})
        print('Prepared supplementary P4-2-2 consumer check.',flush=True);return
    if not a.run:raise RuntimeError('Choose --prepare or --run')
    p=json.loads((out/'protocol.json').read_text())
    for n,h in p['tool_hashes'].items():
        if s.sha(ROOT/'tools'/n)!=h:raise RuntimeError('Prospective tool changed')
    for k,h in p['candidate_hashes'].items():
        if s.sha(ROOT/f'candidates/{k}.yaml')!=h:raise RuntimeError('Candidate changed')
    if s.sha(s.BASE)!=p['base_manifest_sha256']:raise RuntimeError('Base changed')
    rb.MANIFEST=s.BASE;rb.FINGERPRINT=s.BASE.with_suffix('.fingerprint.json')
    path=out/'P4-2-2'
    if path.exists():raise RuntimeError('Preserve existing consumer trial')
    path.mkdir();r=s.run_case(p['scenario'],path,None)
    s.save(out/'summary.json',{'passed':r['passed'],'scenario':'P4-2-2','candidate_executions':0,'S4_trials':0,'main_trials':0,
                            'record':str((path/'record.json').relative_to(ROOT)),'error':r.get('error')})
    print('Supplementary consumer integrity:',r['passed'],r.get('error',''),flush=True)
    if not r['passed']:raise SystemExit(1)
if __name__=='__main__':main()

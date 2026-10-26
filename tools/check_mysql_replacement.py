#!/usr/bin/env python3
"""Repeat rejected S3 MySQL replacement with its required actual SQL readiness gate."""
import argparse,copy,json,time
from pathlib import Path
import scenario_feasibility as s
import reset_baseline as rb

ROOT=rb.ROOT
original_apply=s.apply_changes
SQL_GATE=[]
def apply_with_sql_gate(row,manifest):
    replacement=original_apply(row,manifest)
    deadline=time.monotonic()+90
    while True:
        sample={'at':time.time()}
        try:
            cities=rb.run_db_client('SELECT COUNT(*) FROM cities.cities','shipping')
            ratings=rb.run_db_client('SELECT COUNT(*) FROM ratings.ratings','ratings')
            sample.update(cities=cities,ratings_rows=ratings,passed=cities=='948833' and ratings.isdigit())
        except Exception as e:sample.update(passed=False,error=str(e))
        SQL_GATE.append(sample)
        if sample['passed']:return replacement
        if time.monotonic()>=deadline:raise RuntimeError('Actual Service/account SQL gate did not pass after replacement')
        time.sleep(3)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prepare',action='store_true');ap.add_argument('--run',action='store_true');a=ap.parse_args()
    out=ROOT/'results/S3-mysql-replacement-v2'
    if a.prepare:
        out.mkdir(parents=True,exist_ok=True)
        if (out/'protocol.json').exists():raise RuntimeError('Do not overwrite replacement plan')
        p=json.loads((ROOT/'results/S3-feasibility-v1/protocol.json').read_text())
        row=copy.deepcopy(next(x for x in p['scenarios'] if x['id']=='P2-3-1'))
        source=out/'tool-snapshot';source.mkdir()
        ts=[Path(__file__),ROOT/'tools/scenario_feasibility.py',ROOT/'tools/qualification.py',ROOT/'tools/reset_baseline.py',ROOT/'tools/inject_fault.py']
        for t in ts:(source/t.name).write_bytes(t.read_bytes())
        s.save(out/'protocol.json',{'stage':'S3 corrected setup-readiness repeat','written_at':time.time(),'scenario':row,
          'base_manifest_sha256':s.sha(s.BASE),'candidate_hashes':p['candidate_hashes'],'tool_hashes':{t.name:s.sha(t) for t in ts},
          'previous_rejected_record':'results/S3-feasibility-v1/19-P2-3-1/record.json',
          'reason':'v1 treated Pod Ready as enough before its first healthy SQL query; that query was connection refused. Preserve failure and add the already-required TCP gate.',
          'correction':'After replacement, query via Service from separate db-client using both application accounts; cities=948833 and numeric ratings row count. Retry this setup gate for at most 90s. No data restore during the mutation/fault window.',
          'candidate_executions':0,'S4_trials':0,'main_trials':0})
        print('Prepared corrected MySQL replacement gate.',flush=True);return
    if not a.run:raise RuntimeError('Choose --prepare or --run')
    p=json.loads((out/'protocol.json').read_text())
    for n,h in p['tool_hashes'].items():
        if s.sha(ROOT/'tools'/n)!=h:raise RuntimeError('Prospective tool changed')
    for k,h in p['candidate_hashes'].items():
        if s.sha(ROOT/f'candidates/{k}.yaml')!=h:raise RuntimeError('Candidate changed')
    if s.sha(s.BASE)!=p['base_manifest_sha256']:raise RuntimeError('Base changed')
    rb.MANIFEST=s.BASE;rb.FINGERPRINT=s.BASE.with_suffix('.fingerprint.json');s.apply_changes=apply_with_sql_gate
    path=out/'P2-3-1'
    if path.exists():raise RuntimeError('Preserve existing repeated trial')
    path.mkdir();r=s.run_case(p['scenario'],path,None)
    s.save(path/'actual-sql-readiness.json',SQL_GATE)
    s.save(out/'summary.json',{'passed':r['passed'],'scenario':'P2-3-1','candidate_executions':0,'S4_trials':0,'main_trials':0,
                            'record':str((path/'record.json').relative_to(ROOT)),'error':r.get('error')})
    print('Corrected MySQL replacement:',r['passed'],r.get('error',''),flush=True)
    if not r['passed']:raise SystemExit(1)
if __name__=='__main__':main()

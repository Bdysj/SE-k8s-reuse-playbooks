#!/usr/bin/env python3
"""Prospective supplemental repeat with an explicit final-target-sample time gate."""
import argparse,copy,json,time
from pathlib import Path
from scenario_feasibility import *
import scenario_feasibility as s
import reset_baseline as rb

def run_case(row,path,prior):
    cfg=row['construction'];rec={'scenario':row['id'],'candidate':row['candidate'],'type':'S3_construction_feasibility','started_at':time.time(),'candidate_executed':False,'registered':False,'main_trials':0,'S4_trials':0,'construction':cfg}
    nodes=None
    try:
        if prior is None:reset(path/'initial-reset.log')
        else:save(path/'initial-reset-receipt.json',{'source':str(prior.relative_to(ROOT)),'sha256':sha(prior),'confirmed':True})
        rb.ensure_probe()
        manifest=ROOT/row['healthy_manifest'];rec['replacement']=apply_changes(row,manifest)
        if cfg['remove_node_label']:
            nodes={n['metadata']['name']:n['metadata'].get('labels',{}).get(NODE_KEY) for n in json.loads(rb.kubectl('get','nodes','-o','json',namespaced=False))['items']}
        if nodes:
            rec['node_labels_before']=nodes
            for n in nodes:rb.kubectl('label','node',n,NODE_KEY+'-',namespaced=False)
        deadline=time.monotonic()+90
        while True:
            h=healthy(row,manifest);rec.setdefault('healthy_setup_checks',[]).append(h)
            if h['passed']:break
            if time.monotonic()>=deadline:raise RuntimeError('Changed healthy state failed gate')
            time.sleep(5)
        start=time.monotonic();rec['healthy_window']=[]
        while True:
            h=healthy(row,manifest);rec['healthy_window'].append(h)
            if not h['passed']:raise RuntimeError('Changed healthy state did not remain healthy')
            if time.monotonic()-start>=15:break
            time.sleep(5)
        rec['fault_activation_at']=time.time();activation=time.monotonic();rec['injection']=inject_row(row)
        until=time.monotonic()+90;rec['fault_confirmation']=[]
        while True:
            t=target(row);rec['fault_confirmation'].append(t)
            if not t['healthy']:break
            if time.monotonic()>=until:raise RuntimeError('Intended symptom not observable')
            time.sleep(3)
        start=time.monotonic();rec['fault_observations']=[];rec['non_target_observations']=[]
        while True:
            t=target(row);t['window_seconds']=time.monotonic()-start;rec['fault_observations'].append(t)
            n=non_target(row);rec['non_target_observations'].append(n)
            if t['healthy']:raise RuntimeError('Symptom self-resolved in construction window')
            if not n['passed']:raise RuntimeError('Named non-target checks failed')
            save(path/'record.json',rec)
            if t['window_seconds']>=30:break
            time.sleep(5)
        rec['fault_window_seconds']=time.monotonic()-start
        if row['candidate']=='P1':
            rec['finished_before_expiry']=time.monotonic()-activation<1200
            if not rec['finished_before_expiry']:raise RuntimeError('Timed fault expired')
        rec['construction_passed']=True
    except Exception as e:
        rec.update(construction_passed=False,error=str(e),traceback=traceback.format_exc())
        for name,argv in [('pods',['get','pods']),('deployments',['get','deployments']),('events',['get','events'])]:
            try:save(path/(name+'.json'),rb.kjson(*argv))
            except Exception as de:rec.setdefault('diagnostic_errors',[]).append(str(de))
    finally:
        save(path/'record.json',rec)
        try:
            rb.kubectl('delete','podchaos','pilot-pod-fail','--ignore-not-found','--timeout=60s')
            if nodes is not None:
                for n,v in nodes.items():
                    rb.kubectl('label','node',n,NODE_KEY+'-' if v is None else NODE_KEY+'='+v,'--overwrite',namespaced=False)
                after={n['metadata']['name']:n['metadata'].get('labels',{}).get(NODE_KEY) for n in json.loads(rb.kubectl('get','nodes','-o','json',namespaced=False))['items']}
                rec['node_labels_restored']=after==nodes
                if after!=nodes:raise RuntimeError('Node labels not restored exactly')
            if row['id']=='P5-2-2':rb.kubectl('delete','deployment','web','--ignore-not-found','--wait=true','--timeout=120s')
            reset(path/'post-reset.log');rec['reset_confirmed']=True
        except Exception as e:rec.update(reset_confirmed=False,reset_error=str(e))
        rec['completed_at']=time.time();rec['passed']=rec.get('construction_passed',False) and rec.get('reset_confirmed',False)
        save(path/'record.json',rec)
    return rec

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prepare',action='store_true');ap.add_argument('--run',action='store_true');a=ap.parse_args()
    out=ROOT/'results/S3-observation-window-v2'
    if a.prepare:
        out.mkdir(parents=True,exist_ok=True)
        if (out/'protocol.json').exists():raise RuntimeError('Preserve supplemental temporal plan')
        p=json.loads((ROOT/'results/S3-feasibility-v1/protocol.json').read_text())
        row=copy.deepcopy(next(x for x in p['scenarios'] if x['id']=='P3-3-1'))
        source=out/'tool-snapshot';source.mkdir()
        ts=[Path(__file__),ROOT/'tools/scenario_feasibility.py',ROOT/'tools/qualification.py',ROOT/'tools/reset_baseline.py',ROOT/'tools/inject_fault.py']
        for t in ts:(source/t.name).write_bytes(t.read_bytes())
        save(out/'protocol.json',{'stage':'S3 supplemental temporal evidence','written_at':time.time(),'scenario':row,
            'base_manifest_sha256':sha(s.BASE),'candidate_hashes':p['candidate_hashes'],'tool_hashes':{t.name:sha(t) for t in ts},
            'previous_record':'results/S3-feasibility-v1/32-P3-3-1/record.json',
            'reason':'Original total observation window was 30.3667s but final target sample was 29.5941s. Original construction/reset success is preserved; supplement its temporal evidence without weakening the >=30s audit gate.',
            'correction':'Same helper and construction logic, except end the fault loop only when the last target sample window_seconds is >=30. No extra repair, candidate changes or target-label inference.',
            'candidate_executions':0,'S4_trials':0,'main_trials':0})
        print('Prepared temporal supplement for P3-3-1.',flush=True);return
    if not a.run:raise RuntimeError('Choose --prepare or --run')
    p=json.loads((out/'protocol.json').read_text())
    for n,h in p['tool_hashes'].items():
        if sha(ROOT/'tools'/n)!=h:raise RuntimeError('Prospective source changed')
    for k,h in p['candidate_hashes'].items():
        if sha(ROOT/f'candidates/{k}.yaml')!=h:raise RuntimeError('Candidate changed')
    if sha(s.BASE)!=p['base_manifest_sha256']:raise RuntimeError('Base changed')
    rb.MANIFEST=s.BASE;rb.FINGERPRINT=s.BASE.with_suffix('.fingerprint.json')
    path=out/'P3-3-1'
    if path.exists():raise RuntimeError('Preserve existing temporal trial')
    path.mkdir();r=run_case(p['scenario'],path,None)
    save(out/'summary.json',{'passed':r['passed'],'scenario':'P3-3-1','record':str((path/'record.json').relative_to(ROOT)),
                           'candidate_executions':0,'S4_trials':0,'main_trials':0,'error':r.get('error')})
    print('Temporal supplement:',r['passed'],r.get('error',''),flush=True)
    if not r['passed']:raise SystemExit(1)
if __name__=='__main__':main()

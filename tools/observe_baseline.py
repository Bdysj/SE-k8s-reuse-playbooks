#!/usr/bin/env python3
"""Sustained whole-testbed health/OOM observation; never performance/RQ data.

No application mutation. Create/delete temporary probes only for business checks.
Record raw load failures separately from explicit upstream malformed-payment
requests; do not erase failure totals. Keep each Pod identity unchanged.
"""
import argparse,json,subprocess,time
from pathlib import Path
import reset_baseline as rb


def run(context,namespace,*args):
    p=subprocess.run(['kubectl','--context',context,'-n',namespace,*args],capture_output=True,text=True,timeout=60)
    if p.returncode:raise RuntimeError(p.stderr.strip())
    return p.stdout


def sample(contexts):
    record={'time':time.time(),'clusters':{}}
    for ctx in contexts:
        state={}
        for ns,want in [('robot-shop',13),('chaos-mesh',5)]:
            pods=json.loads(run(ctx,ns,'get','pods','-o','json'))['items']
            if ns=='robot-shop':pods=[p for p in pods if p['metadata'].get('labels',{}).get('service')]
            state[ns]=[{'name':p['metadata']['name'],'uid':p['metadata']['uid'],
                        'ready':bool(p.get('status',{}).get('containerStatuses')) and all(c.get('ready') for c in p.get('status',{}).get('containerStatuses',[])),
                        'restarts':sum(c.get('restartCount',0) for c in p.get('status',{}).get('containerStatuses',[]))} for p in pods]
            assert len(pods)==want,(ctx,ns,len(pods))
            assert all(p['ready'] and p['restarts']==0 for p in state[ns]),(ctx,ns,state[ns])
        state['load']=json.loads(run(ctx,'robot-shop','exec','deploy/load','--','cat','/tmp/load-metrics.json'))
        assert state['load']['users']==5,(ctx,state['load'])
        record['clusters'][ctx]=state
    mem=subprocess.check_output(['docker','exec','k3d-lab-server-0','cat','/proc/meminfo'],text=True,timeout=30)
    record['vm_memory_kib']={line.split(':')[0]:int(line.split()[1]) for line in mem.splitlines() if line.split(':')[0] in ('MemTotal','MemAvailable','SwapTotal','SwapFree')}
    record['host_disk_free_bytes']=__import__('shutil').disk_usage(Path(__file__)).free
    record['docker_stats']=[json.loads(line) for line in subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}'],text=True,timeout=30).splitlines() if line.strip()]
    return record


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--seconds',type=int,default=1800)
    ap.add_argument('--context',action='append',default=None);args=ap.parse_args()
    contexts=args.context or ['k3d-lab','k3d-lab-validation']
    out=Path(__file__).resolve().parent.parent/'results/dual-cluster/post-cleanup-stability';out.mkdir(parents=True,exist_ok=True)
    record={'type':'pilot_stability_observation','main_trials':0,'contexts':contexts,'requested_seconds':args.seconds,'samples':[]}
    start=time.monotonic()
    try:
        while time.monotonic()-start<args.seconds:
            s=sample(contexts)
            if record['samples']:
                first=record['samples'][0]
                for ctx in contexts:
                    for ns in ['robot-shop','chaos-mesh']:
                        assert {p['uid'] for p in first['clusters'][ctx][ns]}=={p['uid'] for p in s['clusters'][ctx][ns]},(ctx,ns,'Pod replacement during stability window')
            record['samples'].append(s);(out/'summary.json').write_text(json.dumps(record,indent=2))
            print(f'Stability {round(time.monotonic()-start)}/{args.seconds}s: all application and injector Pods ready, zero restarts',flush=True)
            time.sleep(min(30,max(0,args.seconds-(time.monotonic()-start))))
        record['seconds_observed']=round(time.monotonic()-start,2);record['business_verification']={}
        for ctx in contexts:
            with (out/(ctx+'-verify.log')).open('w') as f:
                p=subprocess.run([__import__('sys').executable,'-u',str(rb.ROOT/'tools/reset_baseline.py'),'--context',ctx,'--verify'],stdout=f,stderr=subprocess.STDOUT,timeout=180)
            record['business_verification'][ctx]=p.returncode
            assert p.returncode==0,(ctx,'Final business/fingerprint verification failed')
        final_sample=sample(contexts)
        first_sample=record['samples'][0]
        for ctx in contexts:
            for ns in ['robot-shop','chaos-mesh']:
                assert {p['uid'] for p in first_sample['clusters'][ctx][ns]}=={p['uid'] for p in final_sample['clusters'][ctx][ns]},(ctx,ns,'Pod replaced at final verification')
        record['samples'].append(final_sample)
        record['counter_coverage_seconds']=round(final_sample['time']-first_sample['time'],2)
        record['load_summary']={}
        for ctx in contexts:
            metrics=[s['clusters'][ctx]['load'] for s in record['samples']];first,last=metrics[0],metrics[-1]
            deliberate=lambda m:sum(e['failures'] for e in m['entries'] if '[upstream intentional error]' in e['name'])
            delta=last['failures']-first['failures'];expected=deliberate(last)-deliberate(first)
            record['load_summary'][ctx]={'requests':last['requests']-first['requests'],'raw_failures':delta,'upstream_intentional_failures':expected,'other_failures':delta-expected,'max_rss_bytes':max(m['rss_bytes'] for m in metrics),'max_stats_entries':max(m['stats_entries'] for m in metrics)}
            assert last['requests']>first['requests'],(ctx,'Load made no progress')
            assert delta==expected,(ctx,'Unexpected business request failures',delta-expected)
        record['passed']=True
    except Exception as e:record.update(passed=False,error=str(e))
    finally:
        (out/'summary.json').write_text(json.dumps(record,indent=2))
        print(json.dumps({k:v for k,v in record.items() if k!='samples'},indent=2),flush=True)
    return 0 if record.get('passed') else 1


if __name__=='__main__':raise SystemExit(main())

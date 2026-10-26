#!/usr/bin/env python3
"""Healthy control for the payment gate; no fault or repair is performed."""
import argparse,json,subprocess,time
from pathlib import Path
import qualification as q
ap=argparse.ArgumentParser();ap.add_argument('--seconds',type=int,default=240);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
a.output.mkdir(parents=True,exist_ok=False)
q.rb.ensure_probe()
record={'purpose':'Healthy control; no injected fault, no candidate action, no counted qualification/main trial','started_at':time.time(),'samples':[]}
start=time.monotonic()
while True:
    o=q.request('http://payment:8080/health','OK',4.0);o['elapsed']=time.monotonic()-start
    record['samples'].append(o)
    if not o['healthy']:
        for name,cmd in [('payment',['logs','deployment/payment','--timestamps','--since=10m']),
                         ('rabbitmq-alarms',['exec','deployment/rabbitmq','--','rabbitmq-diagnostics','alarms']),
                         ('pods',['get','pods','-o','json']),('chaos',['get','podchaos','-o','json'])]:
            p=subprocess.run(['kubectl','--context','k3d-lab','-n','robot-shop',*cmd],capture_output=True,text=True,timeout=30)
            (a.output/f"{len(record['samples']):03d}-{name}.txt").write_text(p.stdout+'\n'+p.stderr)
    (a.output/'samples.json').write_text(json.dumps(record,indent=2))
    if time.monotonic()-start>=a.seconds:break
    time.sleep(5)
p=subprocess.run(['kubectl','--context','k3d-lab','-n','robot-shop','logs','deployment/payment','--timestamps','--since=15m'],capture_output=True,text=True)
(a.output/'payment-final.log').write_text(p.stdout+p.stderr)
record.update(ended_at=time.time(),elapsed_seconds=time.monotonic()-start,failed_samples=sum(not o['healthy'] for o in record['samples']))
(a.output/'samples.json').write_text(json.dumps(record,indent=2))
q.rb.kubectl('delete','pod','probe','--grace-period=1','--ignore-not-found')
print('Healthy payment samples:',len(record['samples']),'failed:',record['failed_samples'],flush=True)

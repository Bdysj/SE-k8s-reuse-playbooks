#!/usr/bin/env python3
"""Stream required fixed images into assigned nodes in both experiment clusters.

Avoids storing every large development image on all three nodes while preserving
three-node topology and identical image IDs. Preload web/ratings on all nodes:
these are the frozen candidates' repair targets and must not pull during replay.
No build cache or unrelated project data is removed.
"""
import argparse,concurrent.futures,json,shutil,subprocess,time,signal
from pathlib import Path
ap=argparse.ArgumentParser()
ap.add_argument('--contexts',nargs='+',choices=['k3d-lab','k3d-lab-validation'],default=['k3d-lab','k3d-lab-validation'])
ap.add_argument('--output',type=Path,default=Path(__file__).resolve().parent.parent/'results/dual-cluster')
args=ap.parse_args()
OUT=args.output.resolve()
OUT.mkdir(parents=True,exist_ok=True)
stop=False
signal.signal(signal.SIGTERM,lambda *_:globals().__setitem__('stop',True))
seen=set();records=[]


def transfer(pair):
    node,ref=pair
    if shutil.disk_usage(OUT).free < 4*1024**3:
        raise RuntimeError('Host has less than 4GiB free; refusing further image import')
    start=time.monotonic()
    cached=subprocess.run(['docker','exec',node,'crictl','--runtime-endpoint','unix:///run/k3s/containerd/containerd.sock','inspecti',ref],capture_output=True,text=True,timeout=60)
    if cached.returncode==0:
        expected=subprocess.check_output(['docker','image','inspect',ref,'--format','{{.Id}}'],text=True).strip()
        actual=json.loads(cached.stdout)['status']['id']
        if actual!=expected:raise RuntimeError(f'Cached image ID mismatch for {ref} on {node}: {actual} != {expected}')
        return {'node':node,'image':ref,'seconds':0,'already_cached':True}
    with (OUT/(node+'-'+ref.replace('/','_').replace(':','_')+'.log')).open('wb') as log:
        source=subprocess.Popen(['docker','save',ref],stdout=subprocess.PIPE,stderr=log)
        dest=subprocess.Popen(['docker','exec','-i',node,'ctr','--address','/run/k3s/containerd/containerd.sock',
                               '-n','k8s.io','images','import','--all-platforms','-'],stdin=source.stdout,stdout=log,stderr=log)
        source.stdout.close();a=source.wait();b=dest.wait()
        if a or b:raise RuntimeError(f'image import {ref} -> {node} failed: {a}/{b}')
    return {'node':node,'image':ref,'seconds':round(time.monotonic()-start,2)}


try:
    contexts=args.contexts
    nodes=[f'{context}-{role}' for context in contexts for role in ['server-0','agent-0','agent-1']]
    preloads=[(node,ref) for node in nodes for ref in ['ddn-eval/rs-web:arm64','ddn-eval/rs-ratings:arm64']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(transfer,p):p for p in preloads}
        for future in concurrent.futures.as_completed(futures):
            records.append(future.result());seen.add(futures[future])
            (OUT/'image-imports.json').write_text(json.dumps(records,indent=2))
            print(records[-1],flush=True)
        while not stop:
            pods=[]
            for context in contexts:
                try:
                    r=subprocess.run(['kubectl','--context',context,'get','pods','-A','-o','json'],capture_output=True,text=True,timeout=30)
                except subprocess.TimeoutExpired:
                    print(f'{context} API inventory timed out; no successful trial inferred',flush=True);continue
                if r.returncode:continue
                pods.extend(json.loads(r.stdout)['items'])
            needed=set()
            for pod in pods:
                node=pod['spec'].get('nodeName')
                if not node:continue
                for c in pod['spec'].get('containers',[])+pod['spec'].get('initContainers',[]):
                    ref=c['image'].removeprefix('docker.io/')
                    pair=(node,ref)
                    if ref.startswith('ddn-eval/') and pair not in seen:needed.add(pair)
            futures={pool.submit(transfer,p):p for p in needed}
            for future in concurrent.futures.as_completed(futures):
                records.append(future.result());seen.add(futures[future])
                (OUT/'image-imports.json').write_text(json.dumps(records,indent=2))
                print(records[-1],flush=True)
            time.sleep(5)
except Exception as e:
    (OUT/'image-import-error.txt').write_text(str(e))
    print(str(e),flush=True);raise

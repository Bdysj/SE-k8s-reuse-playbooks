"""One-time bootstrap of the obsolete validation work PVC onto its declared role.

Only the validation experiment's disposable MySQL work copy is replaced. The
immutable seed image and incident cluster are untouched. A fixed-role binder
Job provisions an empty volume; the next full reset must populate and confirm it.
Run only while the owning controller is paused and its initial-reset child ended.
"""
import hashlib
import json
import subprocess
import time
from pathlib import Path

import yaml

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'results/replica-development-v1'
CONTEXT='k3d-lab-validation'
RECEIPT=OUT/'volume-bootstrap.json'


def k(*args,input=None,namespace=True):
    p=subprocess.run(['kubectl','--context',CONTEXT]+(['-n','robot-shop'] if namespace else [])+list(args),
                     input=input,capture_output=True,text=True,timeout=150)
    if p.returncode:raise RuntimeError(p.stderr.strip())
    return p.stdout


def main():
    if RECEIPT.exists():raise RuntimeError('Preserve earlier bootstrap receipt')
    aborted=json.loads((OUT/'setup-abort.json').read_text())
    state=subprocess.check_output(['ps','-p',str(aborted['parent_pid']),'-o','stat='],text=True).strip()
    if 'T' not in state:raise RuntimeError('Coordinator is not paused')
    child=subprocess.run(['ps','-p',str(aborted['initial_reset_pid']),'-o','stat='],capture_output=True,text=True)
    if child.returncode==0 and child.stdout.strip() and 'Z' not in child.stdout:
        raise RuntimeError('Initial-reset child still active; do not mutate concurrently')
    docs=[d for d in yaml.safe_load_all((OUT/'qualification.v3.replica.yaml').read_text()) if d]
    mysql=next(d for d in docs if d['kind']=='Deployment' and d['metadata']['name']=='mysql')
    selector=mysql['spec']['template']['spec']['nodeSelector']
    pvc=next(d for d in docs if d['kind']=='PersistentVolumeClaim' and d['metadata']['name']=='mysql-data')
    before=json.loads(k('get','pvc','mysql-data','-o','json'))
    before_pv=json.loads(k('get','pv',before['spec']['volumeName'],'-o','json',namespace=False))
    r={'at':time.time(),'main_trials':0,'type':'replica_work_volume_bootstrap',
       'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
       'old_pvc':before,'old_pv':before_pv,'required_node_selector':selector,
       'immutable_seed_preserved':True,'primary_context_untouched':True,
       'seed_restored_and_verified':False,'passed':False}
    RECEIPT.write_text(json.dumps(r,indent=2)+'\n')
    try:
        k('scale','deployment/mysql','--replicas=0')
        k('wait','--for=delete','pod','-l','service=mysql','--timeout=90s')
        k('delete','pvc','mysql-data','--wait=true','--timeout=120s')
        k('apply','-f','-',input=json.dumps(pvc))
        job={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':'replica-volume-bind'},
             'spec':{'backoffLimit':0,'template':{'spec':{'restartPolicy':'Never',
                'nodeSelector':selector,
                'volumes':[{'name':'work','persistentVolumeClaim':{'claimName':'mysql-data'}}],
                'containers':[{'name':'bind','image':'ddn-eval/mysql-seed:5.7',
                    'imagePullPolicy':'IfNotPresent','volumeMounts':[{'name':'work','mountPath':'/work'}],
                    'command':['sh','-euc','test -d /work && echo EMPTY_WORK_VOLUME_BOUND']} ]}}}}
        k('apply','-f','-',input=json.dumps(job))
        k('wait','--for=condition=complete','job/replica-volume-bind','--timeout=240s')
        if 'EMPTY_WORK_VOLUME_BOUND' not in k('logs','job/replica-volume-bind'):
            raise RuntimeError('Binder completion proof absent')
        r['binder_pods']=json.loads(k('get','pods','-l','job-name=replica-volume-bind','-o','json'))['items']
        r['new_pvc']=json.loads(k('get','pvc','mysql-data','-o','json'))
        r['new_pv']=json.loads(k('get','pv',r['new_pvc']['spec']['volumeName'],'-o','json',namespace=False))
        r['passed']=(r['new_pvc']['status']['phase']=='Bound'
            and r['new_pvc']['metadata']['uid']!=before['metadata']['uid']
            and all(p['spec']['nodeName']==selector['kubernetes.io/hostname'] for p in r['binder_pods']))
        k('delete','job','replica-volume-bind','--wait=true','--timeout=90s')
        if not r['passed']:raise RuntimeError('New private PVC placement not confirmed')
    except Exception as error:
        r.update(error=str(error),passed=False)
        raise
    finally:
        r['completed_at']=time.time();RECEIPT.write_text(json.dumps(r,indent=2)+'\n')
    print('Empty private replica PVC bound to declared role; full reset still required.',flush=True)


if __name__=='__main__':main()

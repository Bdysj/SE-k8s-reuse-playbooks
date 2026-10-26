#!/usr/bin/env python3
"""Write the S2 plan and a seeded-load setup before executing any S2 runs."""
import argparse,hashlib,json,random,re,shlex,time
from pathlib import Path
import yaml
import reset_baseline as rb

ROOT=rb.ROOT
ap=argparse.ArgumentParser()
ap.add_argument('--version',default='qualification-v1')
ap.add_argument('--pin-placement',action='store_true')
ap.add_argument('--local-payment-gateway',action='store_true')
args=ap.parse_args()
if not re.fullmatch(r'qualification-v\d+',args.version):raise SystemExit('Invalid version')
OUT=ROOT/'results'/args.version
OUT.mkdir(parents=True,exist_ok=True)
protocol_path=OUT/'protocol.json'
if protocol_path.exists():
    raise SystemExit('Plan already exists; do not overwrite after observing outcomes.')
registry=yaml.safe_load((ROOT/'candidates/registry.yaml').read_text())
seed=20261001
source=ROOT/'manifests/baseline.arm64.yaml'
manifest=ROOT/'manifests'/args.version.replace('-v','.v')
manifest=manifest.with_suffix(manifest.suffix+'.yaml')
docs=[d for d in yaml.safe_load_all(source.read_text()) if d]
placement={}
if args.pin_placement:
    for p in rb.kjson('get','pods')['items']:
        name=p['metadata'].get('labels',{}).get('service')
        if name in rb.ALL_WORKLOADS and not p['metadata'].get('deletionTimestamp'):
            if name in placement:raise SystemExit('Ambiguous placement: '+name)
            placement[name]=p['spec']['nodeName']
    if set(placement)!=set(rb.ALL_WORKLOADS):raise SystemExit('Missing current workload placement')
    for d in docs:
        if d['kind'] in ('Deployment','StatefulSet'):
            d['spec']['template']['spec'].setdefault('nodeSelector',{})['kubernetes.io/hostname']=placement[d['metadata']['name']]
# Seed the Python PRNG before importing Locust/upstream tasks. Transactions,
# weights, waits, five users, spawn rate, images and resource limits stay fixed.
launcher=("import os,random,runpy,sys; random.seed(20261001); "
          "sys.argv=['locust','-f','/load/grouped-load.py','--host',os.environ['HOST'],"
          "'--headless','-r','1','-u',os.environ['NUM_CLIENTS'],'--only-summary']; "
          "runpy.run_module('locust',run_name='__main__')")
for d in docs:
    if args.local_payment_gateway and d['kind']=='Deployment' and d['metadata']['name']=='payment':
        c=d['spec']['template']['spec']['containers'][0]
        c['env']=[e for e in c.get('env',[]) if e['name']!='PAYMENT_GATEWAY']+[
            {'name':'PAYMENT_GATEWAY','value':'http://user:8080/health'}]
    if d['kind']=='Deployment' and d['metadata']['name']=='load':
        c=d['spec']['template']['spec']['containers'][0]
        c['command']=['sh','-c','exec python -c '+shlex.quote(launcher)+' > /tmp/locust.log 2>&1']
        c.pop('args',None)
manifest.write_text('# Qualification setup: accepted arm64 images/limits; seeded Locust launcher and explicitly declared placement.\n'+
                    ''.join('---\n'+yaml.safe_dump(d,sort_keys=False,width=4096) for d in docs))
rb.MANIFEST=manifest
fingerprint=manifest.with_suffix('.fingerprint.json')
fingerprint.write_text(json.dumps(rb.declared_fingerprint(),indent=2,sort_keys=True))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
order=[{'candidate':c['id'],'arm':a} for c in registry['candidates'] for a in ['no-repair','repair']]
random.Random(seed).shuffle(order)
protocol={
 'version':args.version,'written_at_epoch':time.time(),'purpose':'Independent S2 pairing controls, not S4 or main evaluation',
 'context':'k3d-lab','namespace':'robot-shop','manifest':str(manifest.relative_to(ROOT)),
 'manifest_sha256':sha(manifest),'fingerprint_sha256':sha(fingerprint),'prior_manifest_sha256':sha(source),
 'candidate_hashes':{c['id']:c['candidate_sha256'] for c in registry['candidates']},
 'order_random_seed':seed,'load_python_random_seed':seed,'order':order,
 'placement':placement,'placement_reason':'Keep repeated trials on their current nodes and avoid replicating large image caches across the Mac VM. Same three-node topology, images, five users and per-container resources. nodeSelector still obeys the scheduler and P3 nodeAffinity.',
 'payment_gateway':{'override':args.local_payment_gateway,'endpoint':'http://user:8080/health' if args.local_payment_gateway else 'https://paypal.com/',
                    'contract':'Explicit local substitute for the upstream dummy gateway HTTP-200 call. No financial transaction is performed by this endpoint. Existing payment handler, queue publication and cart deletion remain.',
                    'reason':'Avoid uncontrolled external Internet dependency in a single-worker synchronous payment service. The source consumes PAYMENT_GATEWAY through requests.get without a timeout; this is a versioned lab configuration, not an application upgrade.',
                    'scope':'Gateway networking/reliability and real payment processing are outside this testbed evaluation.'},
 'matching':'Reset fixed MySQL seed, logical Mongo seed, empty Redis and fresh RabbitMQ before each independent arm; identical fault, 5-user workload and PRNG seed.',
 'randomness_limits':'Concurrent task timing and application-generated UUID/ObjectId values are not byte-for-byte deterministic; no identical request trace is claimed.',
 'timing_seconds':{'healthy_warmup':30,'fault_confirmation_budget':90,'pre_repair_persistence':60,
                   'repair_budget':120,'minimum_sustained_recovery':30,'matched_outcome_window':210,'sample_interval':5},
 'target_check':'Through web Service: curl completes, HTTP 200, expected content and <=1s. P3 also requires current-template ReplicaSet/Pod ownership and complete desired rollout, zero target restarts.',
 'no_repair_acceptance':'Every evaluated target observation remains unhealthy throughout the full 210s window after confirmed fault, with no evidence collection errors; timed fault stays unexpired. No repair command or cleanup during window.',
 'repair_acceptance':'Necessary Ansible task events succeed; recovered by 120s after repair starts; all subsequent target observations stay healthy through window end for at least 30s; named non-target checks pass before execution and through recovery window.',
 'non_target_checks':'Direct catalogue /products, shipping /codes, user /uniqueid, payment /health, ratings /api/fetch/Watson; exclude the target service; ready zero-restart Pods outside target; plus all six through-web checks during recovered window.',
 'expiry':'P1 fixed original Pod UID/name, 1200s; record activation/expiry/cleanup. Replacement Pods intentionally outside fixed victim selector; cleanup never counted as repair.',
 'tools':{'ansible-core':'2.19.3','ansible-runner':'2.4.2','PyYAML':'6.0.3','Python':'3.12'},
 'faults':{'P1':'PodChaos pod-failure on one original web Pod, 1200s', 'P2':'ratings replicas=0',
           'P3':'web requests 1m/1Mi, limits 2m/1Mi; predeclared calibrated fault',
           'P4':'web CATALOGUE_HOST=user; consumed at container start', 'P5':'web Service selector service=missing-pilot-target'},
 'retries':'Stop on any rejected arm. Preserve every attempt; fix and version the protocol/tool before a separately labelled retry. Never replace failed evidence.',
 'reset_boundary':'One full reset before first arm and after each arm. A successful immediate post-reset is reused as next-arm setup.',
 'registration':{'S2_only':True,'S3':'pending requirement matrix and eight-change feasibility', 'S4':'pending five successful qualification repeats per candidate', 'main_trials':0}
}
protocol['tool_hashes']={p.name:sha(p) for p in [ROOT/'tools/qualification.py',ROOT/'tools/inject_fault.py',ROOT/'tools/reset_baseline.py',Path(__file__)]}
protocol_path.write_text(json.dumps(protocol,indent=2))
print('Wrote pre-execution S2 protocol, randomized order, candidate hashes and seeded-load manifest.')

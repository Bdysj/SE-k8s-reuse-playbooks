#!/usr/bin/env python3
"""Prospective no-fault diagnostic; no candidate or qualification success counts."""
import hashlib,json,random,re,shlex,subprocess,sys,time
from pathlib import Path
import reset_baseline as rb

ROOT=rb.ROOT
OUT=ROOT/'results/probe-network-control-v1'

def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    if OUT.exists():raise RuntimeError('Preserve prior diagnostic evidence')
    OUT.mkdir(parents=True)
    rb.CONTEXT='k3d-lab';rb.MANIFEST=ROOT/'manifests/qualification.v3.yaml'
    rb.FINGERPRINT=rb.MANIFEST.with_suffix('.fingerprint.json')
    service=rb.kjson('get','service','catalogue')
    ip=service['spec']['clusterIP']
    variants=[{'name':'original-short-name','url':'http://catalogue:8080/products','args':[]},
      {'name':'ipv4-short-name','url':'http://catalogue:8080/products','args':['--ipv4']},
      {'name':'fully-qualified-name','url':'http://catalogue.robot-shop.svc.cluster.local.:8080/products','args':[]},
      {'name':'resolved-current-service-IP','url':'http://catalogue:8080/products','args':['--resolve',f'catalogue:8080:{ip}']}]
    p={'purpose':'No-fault diagnostic of a pre-action catalogue timeout, not candidate repair or main outcome evidence',
       'written_at':time.time(),'duration_seconds':600,'cycle_interval_seconds':5,'curl_max_time':4,
       'exec_timeout_seconds':20,'variants':variants,'order_random_seed':20261004,
       'context':rb.CONTEXT,'manifest':str(rb.MANIFEST.relative_to(ROOT)),
       'manifest_sha256':sha(rb.MANIFEST),'script_sha256':sha(Path(__file__)),
       'candidate_actions':0,'main_trials':0,'S4_successes':0,
       'interpretation':'Record DNS/connect/response timing and content. Transport variants are diagnostic only. No failed qualification is relabelled, timeout extended, or retry hidden.'}
    save(OUT/'protocol.json',p);(OUT/'tool-snapshot.py').write_bytes(Path(__file__).read_bytes())
    record={'protocol_sha256':sha(OUT/'protocol.json'),'samples':[],'errors':[]}
    rng=random.Random(p['order_random_seed'])
    try:
        rb.ensure_probe()
        record['probe']=rb.kjson('get','pod','probe')
        record['resolv_conf']=rb.kubectl('exec','probe','--','cat','/etc/resolv.conf')
        record['curl_version']=rb.kubectl('exec','probe','--','curl','--version')
        start=time.monotonic();record['started_at']=time.time();cycle=0
        while time.monotonic()-start < p['duration_seconds']:
            cycle+=1;order=variants[:];rng.shuffle(order)
            for v in order:
                row={'cycle':cycle,'variant':v['name'],'at':time.time(),'window_seconds':time.monotonic()-start}
                args=['curl','--silent','--show-error','--max-time','4',*v['args'],'--write-out','===TIMING===%{json}',v['url']]
                script=shlex.join(args)+'; probe_rc=$?; printf "\\n===RC===%s" "$probe_rc"'
                try:
                    result=subprocess.run(['kubectl','--context',rb.CONTEXT,'-n',rb.NS,'exec','probe','--','sh','-c',script],capture_output=True,text=True,timeout=20)
                    if result.returncode:raise RuntimeError('probe exec failed: '+result.stderr[:300])
                    body,separator,tail=result.stdout.rpartition('===TIMING===')
                    if not separator:raise RuntimeError('missing curl timing')
                    timing,rc=tail.rsplit('===RC===',1)
                    row.update(timing=json.loads(timing),curl_rc=int(rc.strip()),content_match=bool(re.search(r'"sku"\s*:',body)),body_prefix=body[:100],stderr=result.stderr[:300])
                    row['passed']=row['curl_rc']==0 and row['timing']['http_code']==200 and row['content_match']
                except Exception as error:
                    row.update(passed=False,collection_error=str(error));record['errors'].append(str(error))
                record['samples'].append(row);save(OUT/'record.json',record)
                if not row['passed']:print('DIAGNOSTIC_FAILURE '+json.dumps(row),flush=True)
            if cycle%6==0:print(f"CONTROL {time.monotonic()-start:.1f}/600s cycles={cycle} failures={sum(not x['passed'] for x in record['samples'])}",flush=True)
            time.sleep(min(5,max(0,600-(time.monotonic()-start))))
        record['observed_seconds']=time.monotonic()-start;record['finished_at']=time.time()
        record['per_variant']={v['name']:{'requests':sum(x['variant']==v['name'] for x in record['samples']),
          'failures':sum(x['variant']==v['name'] and not x['passed'] for x in record['samples'])} for v in variants}
    finally:
        rb.kubectl('delete','pod','probe','--ignore-not-found','--grace-period=1')
        record['cleanup_at']=time.time();save(OUT/'record.json',record)
    print(json.dumps(record.get('per_variant',{})),flush=True)
    return 0
if __name__=='__main__':sys.exit(main())

#!/usr/bin/env python3
"""Prospective S3 construction checks; no candidate replay or main outcome labels."""
import argparse,copy,hashlib,json,random,re,shlex,subprocess,sys,time,traceback,uuid
from pathlib import Path
import yaml
import qualification as q
import reset_baseline as rb
from inject_fault import inject

ROOT=rb.ROOT
BASE=ROOT/'manifests/qualification.v3.yaml'
BASE_PROTOCOL=ROOT/'results/qualification-v3/protocol.json'
NODE_KEY='beta.kubernetes.io/os'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def docs():return [d for d in yaml.safe_load_all(BASE.read_text()) if d]
def obj(ds,kind,name):return next(d for d in ds if d['kind']==kind and d['metadata']['name']==name)
def container(d):return d['spec']['template']['spec']['containers'][0]
def env(d,name,value):
    c=container(d);c['env']=[e for e in c.get('env',[]) if e['name']!=name]+[{'name':name,'value':value}]
def deploy_clone(ds,old,new,label=None,remove=False):
    d=copy.deepcopy(obj(ds,'Deployment',old));d['metadata']['name']=new
    if label:
        d['spec']['selector']['matchLabels']={'service':label}
        d['spec']['template']['metadata']['labels']={'service':label}
    if remove:ds.remove(obj(ds,'Deployment',old))
    ds.append(d)
def service_clone(ds,old,new,remove=False):
    d=copy.deepcopy(obj(ds,'Service',old));d['metadata']['name']=new
    if remove:ds.remove(obj(ds,'Service',old))
    ds.append(d)

def variant(row):
    ds=docs();key=row['candidate'];sid=row['id'];wl=q.FAMILY[key]
    cfg={'target_deployment':wl,'target_container':wl,'frontend_service':'web',
         'catalogue_service':'catalogue','required_replicas':1,'fault_replicas':0,
         'fault_memory':'1Mi','replace_pod':None,'recreate_service':None,
         'delete_deployments_before':[],'delete_services_after':[],
         'remove_node_label':False,'cart_check':False,'vote_check':False,
         'target_path':q.PATHS[key][0],'target_pattern':q.PATHS[key][1],
         'endpoint_owner_check':False,'preserve_cart_host':None,'overlapping_conditions':[]}
    if sid.endswith('-1-1'):
        if key=='P5':cfg['recreate_service']='web'
        else:cfg['replace_pod']=wl
    if sid.endswith('-1-2'):
        if key=='P5':
            service_clone(ds,'web','storefront',True);env(obj(ds,'Deployment','load'),'HOST','http://storefront:8080')
            cfg.update(frontend_service='storefront',delete_services_after=['web'])
            cfg['overlapping_conditions']=['referenced_fields','resource_relationships']
        else:
            renamed='feedback' if key=='P2' else 'storefront'
            deploy_clone(ds,wl,renamed,remove=True)
            cfg.update(target_deployment=renamed,delete_deployments_before=[wl])
    if sid.endswith('-3-2') and key!='P5':
        cloned='feedback-live' if key=='P2' else 'frontend-live'
        deploy_clone(ds,wl,cloned,label=cloned)
        obj(ds,'Service',wl)['spec']['selector']={'service':cloned}
        cfg['target_deployment']=cloned
        cfg['overlapping_conditions']=['target_objects','referenced_fields','resource_multiplicity']
    if sid=='P1-2-1':obj(ds,'Deployment','web')['spec'].update(paused=False,strategy={'type':'RollingUpdate','rollingUpdate':{'maxSurge':2,'maxUnavailable':0}})
    if sid=='P1-2-2':obj(ds,'Deployment','web')['spec']['paused']=True
    if sid in ('P1-3-1','P3-3-1'):cfg['recreate_service']='web'
    if sid in ('P1-4-1','P5-4-1'):cfg['target_path']='/index.html'
    if key=='P2' and row['condition']=='referenced_fields':
        minimum=2 if sid.endswith('-1') else 3
        obj(ds,'Deployment','ratings')['spec']['replicas']=minimum
        cfg.update(required_replicas=minimum,fault_replicas=1)
        cfg['overlapping_conditions']=['verification_criteria','resource_multiplicity']
    if sid=='P2-3-1':cfg['replace_pod']='mysql'
    if sid=='P2-4-2':cfg['vote_check']=True
    if sid=='P3-2-1':cfg['fault_memory']='2Mi'
    if sid=='P3-2-2':
        cfg['remove_node_label']=True
        cfg['overlapping_conditions']=['resource_relationships']
    if sid=='P3-4-1':cfg['endpoint_owner_check']=True
    if sid=='P4-2-1':
        d=obj(ds,'Deployment','web');c=container(d)
        c['env']=[{'name':'CART_HOST','value':'cart.robot-shop.svc.cluster.local'},
                  {'name':'CATALOGUE_HOST','value':'catalogue'}]+[e for e in c.get('env',[]) if e['name'] not in ('CART_HOST','CATALOGUE_HOST')]
        cfg['preserve_cart_host']='cart.robot-shop.svc.cluster.local'
    if sid=='P4-2-2':
        service_clone(ds,'catalogue','catalogue-v2',True)
        for n in ['web','cart']:env(obj(ds,'Deployment',n),'CATALOGUE_HOST','catalogue-v2')
        env(obj(ds,'Deployment','ratings'),'CATALOGUE_URL','http://catalogue-v2:8080')
        cfg.update(catalogue_service='catalogue-v2',delete_services_after=['catalogue'])
        cfg['overlapping_conditions']=['target_objects','resource_relationships']
    if sid=='P4-3-1':cfg['replace_pod']='catalogue'
    if sid=='P4-4-1':cfg.update(target_path='/api/catalogue/product/Watson',target_pattern=r'"sku"\s*:\s*"Watson"')
    if sid=='P5-2-1':
        obj(ds,'Deployment','web')['spec']['template']['metadata']['labels']['track']='stable'
        obj(ds,'Service','web')['spec']['selector']['track']='stable'
        cfg['overlapping_conditions']=['resource_relationships']
    if sid=='P5-2-2':
        d=obj(ds,'Deployment','web');d['spec']['selector']['matchLabels']={'app':'web'}
        d['spec']['template']['metadata']['labels']={'app':'web','service':'frontend'}
        obj(ds,'Service','web')['spec']['selector']={'service':'frontend'}
        cfg['delete_deployments_before']=['web']
        cfg['overlapping_conditions']=['target_objects','resource_relationships']
    if sid=='P5-3-1':cfg.update(replace_pod='web',endpoint_owner_check=True)
    if sid=='P5-3-2':
        service_clone(ds,'web','public-web');env(obj(ds,'Deployment','load'),'HOST','http://public-web:8080')
        cfg['frontend_service']='public-web';cfg['overlapping_conditions']=['target_objects','referenced_fields']
    if row['condition']=='verification_criteria' and sid.endswith('-2') and key!='P2':cfg['cart_check']=True
    return ds,cfg

def prepare(out):
    out.mkdir(parents=True,exist_ok=True)
    if (out/'protocol.json').exists():raise RuntimeError('Refusing to overwrite predeclared S3 plan')
    req=yaml.safe_load((ROOT/'candidates/requirements.v1.yaml').read_text())
    source=out/'tool-snapshot';source.mkdir()
    tools=[Path(__file__),ROOT/'tools/qualification.py',ROOT/'tools/inject_fault.py',ROOT/'tools/reset_baseline.py']
    for p in tools:(source/p.name).write_bytes(p.read_bytes())
    scenarios=[]
    for row in req['proposals']:
        ds,cfg=variant(row);p=out/(row['id']+'.healthy.yaml')
        p.write_text(yaml.safe_dump_all(ds,sort_keys=False,width=4096))
        scenarios.append({**row,'construction':cfg,'healthy_manifest':str(p.relative_to(ROOT)),'manifest_sha256':sha(p)})
    random.Random(20261002).shuffle(scenarios)
    plan={'stage':'S3 feasibility only','written_at':time.time(),'base_manifest':str(BASE.relative_to(ROOT)),
          'base_manifest_sha256':sha(BASE),'base_protocol':str(BASE_PROTOCOL.relative_to(ROOT)),
          'requirements_sha256':sha(ROOT/'candidates/requirements.v1.yaml'),'candidate_hashes':req['candidate_hashes'],
          'tool_hashes':{p.name:sha(p) for p in tools},'order_seed':20261002,'scenarios':scenarios,
          'timing':{'healthy_window_seconds':15,'fault_confirmation_budget':90,'fault_observation_seconds':30,'sample_interval':5},
          'gate':'Declared mutation admitted; complete healthy graph, exact image IDs, expected config and current owner-bound verifier pass; intended symptom persists at least 30s; non-target checks remain healthy; node labels and full fixed-seed baseline reset confirmed.',
          'limits':'S3 is feasibility construction, not full-window S2 controls, S4 qualification or formal candidate outcomes. Candidates never execute in this batch. Actual expected effects remain hypotheses.',
          'vote_policy':'Watson PUT must report success; later fetch and Service/account SQL must show count >= initial+1. Concurrent votes permitted; no exact +1 or exclusive causal attribution to this single vote. Counter writes are not atomic; observed inconsistencies must be retained.',
          'cart_policy':'Independent generated cart ID per check, add Watson quantity 2 through current frontend Service, read same cart and require matching SKU/quantity. Never use a direct-Pod shortcut.',
          'node_cleanup':'Restore recorded pre-mutation beta.kubernetes.io/os value on every node and verify exact equality before full reset.',
          'workload':'Same five users, seeded launcher, transactions/waits, ERROR=1 and local dummy gateway as accepted S2 v3. Traffic failures during setup transition are excluded from healthy gate, never from evaluated windows.',
          'retry_policy':'Preserve all attempts. Continue after infeasible case only when reset is confirmed. Revise infeasible proposal/version before main freeze; never drop cases because a candidate outcome differs from expectation.',
          'S4_trials':0,'registered_main_scenarios':0,'main_trials':0}
    save(out/'protocol.json',plan)
    print('Saved prospective S3 plan: 40 healthy manifests, construction/verifier definitions and tool snapshots.',flush=True)

def request(url,pattern='.*',latency=4.0,method='GET'):
    marker='===STATUS==='
    command='curl -s --max-time 4 -X '+shlex.quote(method)+' -w '+shlex.quote(marker+'%{http_code} %{time_total}')+' '+shlex.quote(url)+'; r=$?; printf " ===CURL_RC===%s" "$r"'
    p=subprocess.run(['kubectl','--context',rb.CONTEXT,'-n',rb.NS,'exec','probe','--','sh','-c',command],capture_output=True,text=True,timeout=20)
    if p.returncode:raise RuntimeError('Evidence collection error: '+p.stderr[:300])
    body,sep,tail=p.stdout.rpartition(marker)
    if not sep or '===CURL_RC===' not in tail:raise RuntimeError('Malformed probe output')
    stat,rc=tail.split('===CURL_RC===');bits=stat.split()
    o={'at':time.time(),'url':url,'method':method,'status':int(bits[0]),'seconds':float(bits[1]),'curl_rc':int(rc.strip()),'body':body[:4096]}
    o['healthy']=o['curl_rc']==0 and o['status']==200 and o['seconds']<=latency and bool(re.search(pattern,body,re.S))
    try:o['json']=json.loads(body)
    except ValueError:pass
    return o

def owned_pods(workload,include_terminating=False):
    d=rb.kjson('get','deployment',workload)
    rs={r['metadata']['uid'] for r in rb.kjson('get','replicasets')['items'] if any(o.get('controller') and o.get('uid')==d['metadata']['uid'] for o in r['metadata'].get('ownerReferences',[]))}
    return [p for p in rb.kjson('get','pods')['items'] if any(o.get('controller') and o.get('uid') in rs for o in p['metadata'].get('ownerReferences',[])) and (include_terminating or not p['metadata'].get('deletionTimestamp'))]

def endpoints(service,workload):
    owned={p['metadata']['uid'] for p in owned_pods(workload)}
    slices=rb.kjson('get','endpointslices','-l','kubernetes.io/service-name='+service)['items']
    eps=[ep for s in slices for ep in s.get('endpoints',[]) if ep.get('conditions',{}).get('ready')]
    return {'service':service,'deployment':workload,'owned_pod_uids':sorted(owned),'ready_endpoints':eps,
            'passed':bool(eps) and all(ep.get('targetRef',{}).get('uid') in owned for ep in eps)}

def cart(base):
    cid='s3-'+uuid.uuid4().hex
    a=request(base+'/api/cart/add/'+cid+'/Watson/2');b=request(base+'/api/cart/cart/'+cid)
    def match(o):return o['healthy'] and any(i.get('sku')=='Watson' and i.get('qty')==2 for i in o.get('json',{}).get('items',[]))
    return {'cart_id':cid,'add':a,'read':b,'passed':match(a) and match(b)}

def vote(base):
    before=request(base+'/api/ratings/api/fetch/Watson')
    write=request(base+'/api/ratings/api/rate/Watson/5',method='PUT')
    after=request(base+'/api/ratings/api/fetch/Watson')
    sql=rb.run_db_client("SELECT rating_count FROM ratings.ratings WHERE sku='Watson'",'ratings')
    a=before.get('json',{}).get('rating_count');b=after.get('json',{}).get('rating_count')
    passed=(before['healthy'] and write['healthy'] and after['healthy'] and write.get('json',{}).get('success') is True
            and isinstance(a,int) and isinstance(b,int) and b>=a+1 and sql.isdigit() and int(sql)>=a+1)
    return {'before':before,'write':write,'after':after,'sql_count':sql,'passed':passed}

def target(row):
    cfg=row['construction'];base='http://'+cfg['frontend_service']+':8080'
    o=request(base+cfg['target_path'],cfg['target_pattern'],1.0)
    if row['candidate'] in ('P2','P3'):
        rollout=q.current_rollout(cfg['target_deployment']);o['rollout']=rollout
        if row['candidate']=='P2':
            rollout['minimum_ready_replicas']=cfg['required_replicas']
            o['healthy']=o['healthy'] and rollout['complete'] and rollout['desired']>=cfg['required_replicas']
        else:o['healthy']=o['healthy'] and rollout['complete']
    if row['candidate']=='P2':
        n=o.get('json',{}).get('rating_count')
        o['structured_count_valid']=isinstance(n,int) and not isinstance(n,bool) and n>=0
        o['healthy']=o['healthy'] and o['structured_count_valid']
    if cfg['endpoint_owner_check']:
        e=endpoints(cfg['frontend_service'],cfg['target_deployment']);o['endpoint_owner']=e;o['healthy']=o['healthy'] and e['passed']
    if cfg['cart_check']:
        c=cart(base);o['cart']=c;o['healthy']=o['healthy'] and c['passed']
    # Skip writes when the basic symptom is already visible (e.g. zero replicas).
    if cfg['vote_check'] and o['healthy']:
        v=vote(base);o['vote']=v;o['healthy']=o['healthy'] and v['passed']
    if cfg['preserve_cart_host']:
        d=rb.kjson('get','deployment',cfg['target_deployment']);es=container(d).get('env',[])
        o['cart_host_preserved']=any(e['name']=='CART_HOST' and e.get('value')==cfg['preserve_cart_host'] for e in es)
        o['healthy']=o['healthy'] and o['cart_host_preserved']
    return o

def non_target(row):
    cfg=row['construction'];key=row['candidate'];excluded={p['metadata']['uid'] for p in owned_pods(cfg['target_deployment'],True)}
    bad=[p['metadata']['name'] for p in rb.kjson('get','pods')['items'] if p['metadata']['name'] not in ('probe','db-client') and p['metadata']['uid'] not in excluded and not q.ready(p)]
    checks=[]
    for n,url,pattern in q.DIRECT:
        if key=='P2' and n=='ratings':continue
        if n=='catalogue':url='http://'+cfg['catalogue_service']+':8080/products'
        checks.append({'name':n,**request(url,pattern)})
    return {'checks':checks,'unhealthy_pods':bad,'passed':not bad and all(c['healthy'] for c in checks)}

def healthy(row,manifest):
    cfg=row['construction'];base='http://'+cfg['frontend_service']+':8080'
    old=rb.MANIFEST;rb.MANIFEST=manifest
    try:diffs=rb.diff_fingerprint(rb.declared_fingerprint(),rb.live_fingerprint())
    finally:rb.MANIFEST=old
    pods=[p for p in rb.kjson('get','pods')['items'] if p['metadata']['name'] not in ('probe','db-client')]
    bad=[p['metadata']['name'] for p in pods if not q.ready(p)]
    images=rb.verify_local_image_ids(pods)
    checks=[{'name':n,**request(base+path,pattern)} for n,path,code,pattern in rb.CHECKS]
    t=target(row);binding=endpoints('ratings' if row['candidate']=='P2' else cfg['frontend_service'],cfg['target_deployment'])
    sql=rb.run_db_client('SELECT COUNT(*) FROM cities.cities')
    extra=True;fields={}
    if row['candidate']=='P1' and row['condition']=='referenced_fields':
        spec=rb.kjson('get','deployment',cfg['target_deployment'])['spec']
        fields={'paused':spec.get('paused',False),'strategy':spec.get('strategy',{})}
        desired=next(d for d in yaml.safe_load_all(manifest.read_text()) if d and d['kind']=='Deployment' and d['metadata']['name']==cfg['target_deployment'])['spec']
        extra=fields['paused']==desired.get('paused',False)
        if row['id']=='P1-2-1':extra=extra and fields['strategy']==desired['strategy']
    if cfg['remove_node_label']:
        fields['node_legacy_labels']={n['metadata']['name']:n['metadata'].get('labels',{}).get(NODE_KEY) for n in json.loads(rb.kubectl('get','nodes','-o','json',namespaced=False))['items']}
        extra=extra and all(v is None for v in fields['node_legacy_labels'].values())
    return {'at':time.time(),'fingerprint_differences':diffs,'unhealthy_pods':bad,'image_id_errors':images,'business':checks,'target':t,'owner_binding':binding,'seed_cities':sql,'additional_fields':fields,
            'passed':extra and not(diffs or bad or images) and sql=='948833' and all(c['healthy'] for c in checks) and t['healthy'] and binding['passed']}

def apply_changes(row,manifest):
    cfg=row['construction'];baseline=docs();changed=[d for d in yaml.safe_load_all(manifest.read_text()) if d]
    for n in cfg['delete_deployments_before']:rb.kubectl('delete','deployment',n,'--wait=true','--timeout=120s')
    if cfg['recreate_service']:rb.kubectl('delete','service',cfg['recreate_service'],'--wait=true','--timeout=60s')
    for d in changed:
        old=next((x for x in baseline if x['kind']==d['kind'] and x['metadata']['name']==d['metadata']['name']),None)
        if d!=old or d['metadata']['name'] in cfg['delete_deployments_before'] or (d['kind']=='Service' and d['metadata']['name']==cfg['recreate_service']):rb.apply_object(d)
    for n in cfg['delete_services_after']:rb.kubectl('delete','service',n,'--wait=true','--timeout=60s')
    for d in changed:
        if d['kind']=='Deployment':rb.kubectl('rollout','status','deployment/'+d['metadata']['name'],'--timeout=180s') if not d['spec'].get('paused') else None
    replacement=None
    if cfg['replace_pod']:
        wl=cfg['replace_pod'];ps=owned_pods(wl)
        if len(ps)!=1:raise RuntimeError('Replacement feasibility requires one owned victim')
        victim=ps[0];rb.kubectl('delete','pod',victim['metadata']['name'],'--wait=true','--timeout=120s')
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            new=owned_pods(wl)
            if len(new)==1 and q.ready(new[0]) and new[0]['metadata']['uid']!=victim['metadata']['uid']:
                replacement={'workload':wl,'old_name':victim['metadata']['name'],'old_uid':victim['metadata']['uid'],'new_name':new[0]['metadata']['name'],'new_uid':new[0]['metadata']['uid']};break
            time.sleep(3)
        if replacement is None:raise RuntimeError('Owned replacement did not become ready')
    return replacement

def inject_row(row):
    cfg=row['construction'];wl=cfg['target_deployment'];c=cfg['target_container'];key=row['candidate']
    if key=='P1':return inject('pod-failure',wl,c)
    if key=='P2':
        rb.kubectl('scale','deployment',wl,'--replicas='+str(cfg['fault_replicas']));return {'kind':'underprovision','deployment':wl,'replicas':cfg['fault_replicas']}
    if key=='P3':return inject('pod-config-error',wl,c,resource_values={'requests':{'cpu':'1m','memory':cfg['fault_memory']},'limits':{'cpu':'2m','memory':cfg['fault_memory']}})
    if key=='P4':
        rb.kubectl('set','env','deployment/'+wl,'CATALOGUE_HOST=user');rb.kubectl('rollout','status','deployment/'+wl,'--timeout=120s')
        return {'kind':'wrong consumed host','deployment':wl,'variable':'CATALOGUE_HOST','value':'user'}
    rb.kubectl('patch','service',cfg['frontend_service'],'--type=merge','-p',json.dumps({'spec':{'selector':{'service':'missing-s3-target'}}}))
    return {'kind':'wrong selector','service':cfg['frontend_service'],'other_selector_keys_preserved':True}

def reset(log):
    with log.open('w') as f:
        p=subprocess.run([sys.executable,'-u',str(ROOT/'tools/reset_baseline.py'),'--context',rb.CONTEXT,'--manifest',str(BASE),'--tier-timeout','600'],stdout=f,stderr=subprocess.STDOUT,timeout=1500)
    if p.returncode:raise RuntimeError('Reset failed; see '+str(log))

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
            if time.monotonic()-start>=30:break
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
    ap=argparse.ArgumentParser();ap.add_argument('--prepare',action='store_true');ap.add_argument('--run',action='store_true');ap.add_argument('--output',type=Path,default=ROOT/'results/S3-feasibility-v1');args=ap.parse_args();out=args.output.resolve()
    if args.prepare:prepare(out);return 0
    if not args.run:raise RuntimeError('Choose --prepare or --run')
    plan=json.loads((out/'protocol.json').read_text())
    if sha(BASE)!=plan['base_manifest_sha256']:raise RuntimeError('Base setup changed')
    for name,h in plan['tool_hashes'].items():
        if sha(ROOT/'tools'/name)!=h:raise RuntimeError('Tool changed after prospective plan: '+name)
    for k,h in plan['candidate_hashes'].items():
        if sha(ROOT/f'candidates/{k}.yaml')!=h:raise RuntimeError('Candidate changed')
    rb.MANIFEST=BASE;rb.FINGERPRINT=BASE.with_suffix('.fingerprint.json')
    prior=None;records=[]
    for i,row in enumerate(plan['scenarios']):
        path=out/f"{i+1:02d}-{row['id']}"
        if path.exists():raise RuntimeError('Refusing to overwrite '+str(path))
        if sha(ROOT/row['healthy_manifest'])!=row['manifest_sha256']:raise RuntimeError('Healthy manifest changed')
        path.mkdir();print(f"START {i+1}/40 {row['id']}",flush=True)
        rec=run_case(row,path,prior);records.append(rec)
        prior=path/'post-reset.log' if rec.get('reset_confirmed') else None
        save(out/'summary.json',{'stage':'S3','completed':len(records),'required':40,'feasible':sum(r['passed'] for r in records),
            'passed':len(records)==40 and all(r['passed'] for r in records),'candidate_executions':0,'S4_trials':0,'registered_main_scenarios':0,'main_trials':0,
            'records':[{'scenario':r['scenario'],'passed':r['passed'],'error':r.get('error'),'reset_confirmed':r.get('reset_confirmed')} for r in records]})
        print(f"END {row['id']} feasible={rec['passed']} {rec.get('error','')}",flush=True)
        if not rec.get('reset_confirmed'):return 1
    return 0 if all(r['passed'] for r in records) else 1

if __name__=='__main__':sys.exit(main())

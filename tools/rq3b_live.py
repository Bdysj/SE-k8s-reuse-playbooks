"""Registered short RQ3b sequences. Physical adapters; no per-incident reset.

The scope is one Service-routing repair family. Historical sources/results stay
unchanged. Replanning and every shared fallback use the same fixed model call.
"""
import argparse
import ast
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

import yaml
import reset_baseline as rb
import main_verifier as v
import scenario_feasibility as s
import qualification as q
import registration_trials as reg
import runner_control
import supported_reuse_v2 as supported
import reuse_assessor as assessor
from reuse_validation import strict_application
from prepare_replica_development import translate
from run_replica_development import volumes, docker_roots

# Script entry points import Project's namespace packages without changing frozen helpers.
sys.path.insert(0, str(rb.ROOT))
from offline_work.rq3b_live_v1.engine import Engine, Limits, Plan, UnsafeContinuation
from offline_work.rq3b_live_v1.api import plan as model_plan

ROOT = rb.ROOT
BASE = ROOT / 'results/reuse-flow-development-v8/07-revalidate-P5/primary/baseline.yaml'
REPLICA_BASE = ROOT / 'results/reuse-flow-development-v8/07-revalidate-P5/replica/baseline.yaml'
MARKER = 'rq3b-preserved-cart'


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def digest(value): return assessor.digest(value)
def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n'); temp.replace(path)


@contextmanager
def context(name):
    before = rb.CONTEXT, rb.MANIFEST, rb.FINGERPRINT
    rb.CONTEXT = name; rb.MANIFEST = BASE if name == 'k3d-lab' else REPLICA_BASE
    rb.FINGERPRINT = rb.MANIFEST.with_suffix('.fingerprint.json')
    try: yield
    finally: rb.CONTEXT, rb.MANIFEST, rb.FINGERPRINT = before


def restore(name, path, budget=1500):
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = BASE if name == 'k3d-lab' else REPLICA_BASE
    # A declared label-remapping case changes Deployment's immutable selector.
    # Recreate only that workload at the reset boundary; keep all PVCs intact.
    with context(name):
        declared=next(d for d in yaml.safe_load_all(manifest.read_text())
                      if d and d.get('kind')=='Deployment' and d['metadata']['name']=='web')
        live=rb.kjson('get','deployment','web',allow_fail=True)
        changed=bool(live and live['spec']['selector']!=declared['spec']['selector'])
        receipt={'at':time.time(),'context':name,'immutable_selector_changed':changed,
                 'live_selector':live.get('spec',{}).get('selector'),
                 'declared_selector':declared['spec']['selector'],'removed_workloads':[]}
        if changed:
            # Quiesce the load before recreating the frontend during reset.
            rb.kubectl('scale','deployment/load','--replicas=0')
            rb.kubectl('delete','deployment/web','--cascade=foreground','--wait=true','--timeout=120s')
            receipt['removed_workloads']=['Deployment/web']
        save(path.with_suffix('.selector-reset.json'),receipt)
    with path.open('x') as log:
        result = subprocess.run([sys.executable, '-B', '-u', str(ROOT/'tools/reset_baseline.py'),
            '--context', name, '--manifest', str(manifest), '--tier-timeout', '600'],
            stdout=log, stderr=subprocess.STDOUT, timeout=budget)
    if result.returncode: raise RuntimeError('Baseline reset rejected: ' + str(path))


def marker(create=False):
    if create: s.request('http://web:8080/api/cart/add/' + MARKER + '/Watson/2')
    value = s.request('http://web:8080/api/cart/cart/' + MARKER)
    items = value.get('json', {}).get('items', [])
    if not value['healthy'] or not any(x.get('sku') == 'Watson' and x.get('qty') == 2 for x in items):
        raise RuntimeError('Retained business cart state unavailable')
    return {'cart_id': MARKER, 'items': items}


def row_for(template, incident):
    source = json.loads((ROOT/'results/reuse-flow-development-v8/07-revalidate-P5/primary/protocol.json').read_text())
    row = copy.deepcopy(source['scenarios'][0]); row['id'] = template + '-' + str(incident)
    row['condition'] = 'control' if incident == 1 or template == 'unchanged' else 'referenced_fields'
    row['construction'].update(cart_check=False, vote_check=False, delete_deployments_before=[])
    return row


def change(template, path):
    if template == 'unchanged': return
    dep = rb.kjson('get', 'deployment', 'web')
    if template == 'preserving':
        rb.kubectl('patch', 'deployment', 'web', '--type=merge', '-p',
                   json.dumps({'spec': {'template': {'metadata': {'labels': {'track': 'stable'}}}}}))
        rb.kubectl('rollout', 'status', 'deployment/web', '--timeout=180s')
        rb.kubectl('patch', 'service', 'web', '--type=merge', '-p',
                   json.dumps({'spec': {'selector': {'track': 'stable'}}}))
    elif template == 'invalidating':
        # Equivalent to admitted P5-2-2. Deployment replacement is a declared change,
        # not a state reset. Data stores, cart and other application resources remain.
        dep.pop('status', None)
        dep['metadata'] = {'name': 'web', 'namespace': rb.NS}
        dep['spec']['selector']['matchLabels'] = {'app': 'web'}
        dep['spec']['template']['metadata']['labels'] = {'app': 'web', 'service': 'frontend'}
        rb.kubectl('delete', 'deployment', 'web', '--wait=true', '--timeout=120s')
        rb.apply_object(dep)
        rb.kubectl('rollout', 'status', 'deployment/web', '--timeout=180s')
        rb.kubectl('patch', 'service', 'web', '--type=merge', '-p',
                   json.dumps({'spec': {'selector': {'service': 'frontend'}}}))
    else: raise ValueError('Unknown template')
    # A Service patch returns before EndpointSlice propagation completes.
    # This is a healthy change interval, outside incident recovery timing.
    ready_by=time.monotonic()+60
    while not v.target(row_for(template,2))['healthy']:
        if time.monotonic()>=ready_by:raise RuntimeError('Declared change did not become healthy')
        time.sleep(2)
    save(path, {'at': time.time(), 'template': template, 'deployment': rb.kjson('get','deployment','web'),
                'service': rb.kjson('get','service','web'), 'preserved_cart': marker()})


def check_plan(plan, context_name):
    """The same scoped gate admits historical and newly generated selector plans."""
    docs = yaml.safe_load(plan.content)
    if not isinstance(docs, list) or len(docs) != 1: raise ValueError('One play only')
    play = docs[0]
    if set(play) - {'name','hosts','gather_facts','tasks'} or play.get('hosts') not in ('localhost','k8s_nodes') or play.get('gather_facts') is not False:
        raise ValueError('Unexpected play execution scope')
    tasks = play.get('tasks')
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 4: raise ValueError('Bounded tasks required')
    mutations = []
    for task in tasks:
        if not isinstance(task, dict) or set(task) - {'name','ansible.builtin.command'}:
            raise ValueError('Only command argv tasks are admitted')
        command = task.get('ansible.builtin.command')
        if not isinstance(command, dict) or set(command) != {'argv'}: raise ValueError('Literal argv required')
        argv = command['argv']
        if not isinstance(argv, list) or not all(isinstance(x,str) for x in argv): raise ValueError('String argv required')
        argv = [x.replace('{{ kube_context }}', context_name) for x in argv]
        if any('{{' in x or '{%' in x for x in argv): raise ValueError('No dynamic command interpolation')
        if not argv or argv.pop(0) != 'kubectl': raise ValueError('Only kubectl allowed')
        # Normalise equivalent literal CLI spellings before applying the same
        # allowlist. Split only once so '=' inside JSON values stays literal.
        literal_options=('--context','-n','--namespace','--type','-p','--patch')
        argv=[part for item in argv for part in
              (item.split('=',1) if any(item.startswith(option+'=') for option in literal_options) else [item])]
        for options, expected in [(('--context',),context_name),(('-n','--namespace'),rb.NS)]:
            found = [i for i,x in enumerate(argv) if x in options]
            if len(found) != 1: raise ValueError('Explicit unique context and namespace required')
            i=found[0]
            if i+1 >= len(argv) or argv[i+1] != expected: raise ValueError('Context/namespace differs')
            del argv[i:i+2]
        if argv[:3] not in (['patch','service','web'], ['patch','svc','web']): raise ValueError('Only declared Service selector mutation allowed')
        args=argv[3:]
        if len(args)!=4:raise ValueError('Literal bounded patch and explicit type required')
        keys={'--type':'type','-p':'patch','--patch':'patch'};options={}
        for index in (0,2):
            key=keys.get(args[index])
            if key is None or key in options:raise ValueError('Unknown or duplicate patch option')
            options[key]=args[index+1]
        patch_type=options.get('type')
        if patch_type not in ('merge','json'):raise ValueError('Only explicit merge or JSON selector patches allowed')
        if 'patch' not in options:raise ValueError('Literal bounded patch required')
        patch=json.loads(options['patch'])
        if patch_type=='json':
            if not isinstance(patch,list) or not patch:raise ValueError('JSON patch operations required')
            for operation in patch:
                path=operation.get('path','')
                if operation.get('op') not in ('add','replace','remove') or not (path=='/spec/selector' or path.startswith('/spec/selector/')):
                    raise ValueError('JSON patch outside selector scope')
                if set(operation)-{'op','path','value'}:raise ValueError('Unsupported JSON patch keys')
                if operation['op']!='remove':
                    value=operation.get('value')
                    if path=='/spec/selector':
                        if not isinstance(value,dict) or not value or not all(isinstance(k,str) and isinstance(v,str) for k,v in value.items()):raise ValueError('String selector values required')
                    elif not isinstance(value,str):raise ValueError('String selector value required')
            mutations.append(patch);continue
        if set(patch)!={'spec'} or set(patch['spec'])!={'selector'}: raise ValueError('Only selector keys may change')
        selector=patch['spec']['selector']
        if not isinstance(selector,dict) or not selector or not all(isinstance(k,str) and isinstance(x,str) for k,x in selector.items()):
            raise ValueError('Nonempty string selectors required')
        mutations.append(selector)
    return mutations


class Port:
    def __init__(self, output, protocol, template, role):
        self.output,self.protocol,self.template,self.role = output,protocol,template,role
        self.history=[]; self.number=0; self.obs_number=0; self.plan_number=0; self.attempt_number=0
        self.deadline=None; self.observations={}; self.last_execution=None
    def registration_verified(self):
        return self.protocol['scope_frozen'] and self.protocol['candidate_sha256']==sha(ROOT/'candidates/P5.yaml')
    def restore(self, identity):
        self.output.mkdir(parents=True,exist_ok=False)
        restore('k3d-lab',self.output/'initial-reset.log')
        with context('k3d-lab'):
            rb.ensure_probe()
            self.starting_specs = strict_application('k3d-lab',BASE)
            state = marker(create=True)
            save(self.output/'starting-state.json', {'specs':self.starting_specs,'preserved_cart':state})
        self.expected_marker = state
        return {'passed':True,'start_identity':identity,'fingerprint':digest({'manifest':sha(BASE),'cart':state})}
    def healthy_interval(self, seconds):
        with context('k3d-lab'):
            if self.number and marker()!=self.expected_marker: raise UnsafeContinuation('Cart state changed between incidents')
            if seconds: time.sleep(seconds)
    def present(self, incident):
        self.number += 1; self.path=self.output/f'incident-{self.number:02d}'; self.path.mkdir()
        self.row=row_for(self.template,self.number)
        self.contract=assessor.contract(self.row,str((self.output.parent/'protocol.json').relative_to(ROOT)),sha(self.output.parent/'protocol.json'))
        with context('k3d-lab'):
            if self.number==2: change(self.template,self.path/'declared-change.json')
            if not v.target(self.row)['healthy'] or not v.non_target(self.row)['passed']:
                raise UnsafeContinuation('Healthy pre-incident gate failed')
            self.pre_marker=marker()
            if self.pre_marker!=self.expected_marker: raise UnsafeContinuation('Within-sequence data state lost')
            self.healthy_spec=strict_application('k3d-lab',BASE)
            start=time.time(); self.deadline=start+self.protocol['response_cap_seconds']
            self.injection=s.inject_row(self.row)
            samples=[]; end=time.monotonic()+60
            while True:
                target=v.target(self.row); nt=v.non_target(self.row)
                samples.append({'at':time.time(),'target':target,'non_target':nt})
                if target['healthy'] or not nt['passed']: raise RuntimeError('Fault persistence gate failed')
                if time.monotonic()>=end: break
                time.sleep(5)
            save(self.path/'presentation.json',{'presented_at':start,'injection':self.injection,
                 'pre_marker':self.pre_marker,'fault_persistence':samples,'contract':self.contract})
        return {'presented_at':start}
    def collect(self, remaining):
        self.obs_number+=1
        with context('k3d-lab'):
            raw=supported.collect('P5',self.contract)
            result={'at':raw['completed_at'],'revision':raw['relevant_revision'],
                    'contract':digest(self.contract),'evidence_id':str((self.path/f'observation-{self.obs_number:03d}.json').relative_to(ROOT))}
            save(ROOT/result['evidence_id'],raw); self.observations[result['evidence_id']]=raw
        return result
    def assess(self, plan, observation, history, remaining):
        raw=self.observations[observation['evidence_id']]
        with context('k3d-lab'):
            states=supported.compare('P5',plan.sha256,raw,self.contract,ttl=30)
        decision=assessor.decision(states)['decision']
        save(self.path/'assessment.json',{'at':time.time(),'states':states,'decision':decision,'history':history})
        return decision
    def plan(self, observation, remaining):
        self.plan_number+=1
        raw=self.observations[observation['evidence_id']]
        objects=[]
        for field,kind in [('deployments','Deployment'),('services','Service'),('pods','Pod')]:
            for item in raw[field]:
                md=item['metadata']
                if md['name']!='web' and not(kind=='Pod' and v.controlled_by(item,'ReplicaSet',next((rs['metadata']['uid'] for rs in raw['replicasets'] if v.controlled_by(rs,'Deployment',v.find(raw,'deployments','web')['metadata']['uid'])),''))): continue
                objects.append({'apiVersion':item['apiVersion'],'kind':kind,
                    'metadata':{k:md[k] for k in ('name','namespace','labels') if k in md},
                    'spec':item.get('spec',{}),'status':item.get('status',{})})
        with context('k3d-lab'): symptom=v.target(self.row)
        envelope={'schema_version':'rq3b-live-permitted-v1','source_mode':'fresh live observations',
            'context':'k3d-lab','namespace':rb.NS,
            'repair_requirement':'Restore HTTP access through Service web without changing ports or unrelated resources.',
            'allowed_mutation_targets':[{'kind':'Service','name':'web','namespace':rb.NS}],
            'current_objects':objects,'observed_http_symptom':{k:symptom[k] for k in ('url','method','status','seconds','curl_rc','body') if k in symptom},
            'past_candidate_code_included':False,'future_repair_outcome_included':False}
        value=model_plan(self.path/f'planning-{self.plan_number:02d}',envelope,min(remaining,self.deadline-time.time()))
        if value['status']!='candidate': raise ValueError('Planner returned unresolved')
        return Plan('generated-'+str(self.plan_number),value['plan_yaml'],'generated')
    def static_check(self, plan, remaining):
        self.attempt_number+=1
        path=self.path/f'candidate-{self.attempt_number:02d}';path.mkdir();(path/'plan.yaml').write_text(plan.content)
        self.candidate_path=path
        try:
            check_plan(plan,'k3d-lab')
            result=subprocess.run(['ansible-playbook','--syntax-check','-i',str(ROOT/'tools/ansible-inventory.ini'),str(path/'plan.yaml')],capture_output=True,text=True,timeout=min(30,remaining))
            passed=result.returncode==0
            save(path/'static-check.json',{'passed':passed,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
            return passed
        except Exception as error:
            save(path/'static-check.json',{'passed':False,'error':str(error)});return False
    def safety_gate(self, plan, observation, remaining):
        raw=self.observations[observation['evidence_id']]
        try:
            check_plan(plan,'k3d-lab')
            pods=raw['pods']; namespaces={x['metadata'].get('namespace') for x in pods}
            passed=namespaces <= {rb.NS} and any(x['metadata']['name']=='web' for x in raw['services'])
        except Exception: passed=False
        save(self.candidate_path/'common-gate.json',{'passed':passed,'plan_sha256':plan.sha256,'revision':observation['revision'],'context':'k3d-lab','namespace':rb.NS})
        return passed
    def execute(self, plan, permission, remaining):
        with context('k3d-lab'):
            current=supported.collect('P5',self.contract)
            if current['relevant_revision']!=permission['revision'] or time.time()>=permission['expires_at']:
                return {'executed':False,'complete':False}
            self.before_action=v.non_target(self.row)
            if not self.before_action['passed'] or time.time()>=permission['expires_at']:
                return {'executed':False,'complete':False}
            self.fault_selector=copy.deepcopy(rb.kjson('get','service','web')['spec']['selector'])
            record=self.run_action(plan,self.candidate_path,'k3d-lab',min(120,remaining))
        self.last_execution=record
        return record
    def run_action(self, plan, path, context_name, cap):
        check_plan(plan,context_name)
        (path/'plan.yaml').write_text(plan.content)
        (path/'runner').mkdir(parents=True,exist_ok=False)
        start=time.time()
        thread,runner=runner_control.run_async(private_data_dir=str(path/'runner'),project_dir=str(ROOT),
            playbook=str(path/'plan.yaml'),inventory=str(ROOT/'tools/ansible-inventory.ini'),
            extravars={'kube_context':context_name},quiet=True,process_isolation=False,
            settings={'job_timeout':cap,'idle_timeout':cap},envvars={'ANSIBLE_HOST_KEY_CHECKING':'False',
                'DDN_LLM_API_KEY':'','OPENAI_API_KEY':''})
        thread.join(timeout=cap)
        if thread.is_alive(): runner.cancel()
        result=q.execution_result(runner)
        if plan.origin=='historical':
            runner.candidate_id='P5'; result=reg.execution_s4(runner)
        result.update(executed=True,complete=result['execution_complete'],action_started_at=start,
                      finished_at=time.time(),plan_sha256=plan.sha256,runner_termination_confirmed=not thread.is_alive())
        save(path/'execution.json',result)
        if thread.is_alive(): raise UnsafeContinuation('Runner still active')
        return result
    def verify(self, plan, execution, remaining):
        return self.verify_action(plan,execution,self.candidate_path,'k3d-lab',remaining)
    def verify_action(self, plan, execution, path, context_name, remaining):
        start=time.monotonic();samples=[];posts=[];first=None;lost=False
        with context(context_name):
            before=self.before_action if context_name=='k3d-lab' else self.isolated_before_action
            while True:
                elapsed=time.monotonic()-start
                target=v.target(self.row);nt=v.non_target(self.row)
                samples.append({'at':time.time(),'window_seconds':elapsed,**target});posts.append(nt)
                if target['healthy'] and first is None: first=elapsed
                elif first is not None and not target['healthy']: lost=True
                save(path/'verification-window.json',{'samples':samples,'post_action_non_target':posts})
                if elapsed>=210:break
                if time.monotonic()-start>=remaining:raise RuntimeError('Response deadline before complete verification')
                time.sleep(min(5,210-elapsed))
            complete=samples[-1]['window_seconds']>=210
            recovered=first is not None and first<=120 and not lost and samples[-1]['window_seconds']-first>=30
            regression,_=v.regression(before,posts,complete)
            outcome=v.outcome(execution['complete'],recovered,regression,execution['executed'])
            retained=marker() if recovered else None
            if recovered and retained!=self.expected_marker: raise UnsafeContinuation('Business state lost by repair')
        record={'outcome':outcome,'target_recovered':recovered,'non_target_passed':regression is False,
            'window_complete':complete,'executed':execution['executed'], 'outcome_fixed_at':time.time(),
            'evidence_id':str((path/'verified-outcome.json').relative_to(ROOT)),'preserved_cart':retained}
        save(ROOT/record['evidence_id'],record);return record
    def reconcile(self, plan, execution, remaining):
        if not execution.get('runner_termination_confirmed', self.last_execution and self.last_execution.get('runner_termination_confirmed')):
            raise UnsafeContinuation('Unconfirmed runner; no rollback or next action')
        with context('k3d-lab'):
            rb.kubectl('patch','service','web','--type=json','-p',json.dumps([{'op':'replace','path':'/spec/selector','value':self.fault_selector}]))
            nt=v.non_target(self.row);fault=not v.target(self.row)['healthy']
        save(self.candidate_path/'rollback.json',{'at':time.time(),'non_target_passed':nt['passed'],'fault_still_present':fault,'selector':self.fault_selector})
        return nt['passed'] and fault
    def make_replica(self, plan, observation, remaining):
        path=self.path/'isolated';path.mkdir()
        with context('k3d-lab'):
            primary_before=strict_application('k3d-lab',BASE)
            observed=supported.collect('P5',self.contract)
        if observed['relevant_revision']!=observation['revision']:raise RuntimeError('Primary changed before replay')
        restore('k3d-lab-validation',path/'reset.log',min(1500,remaining))
        with context('k3d-lab-validation'):
            rb.ensure_probe()
            marker(create=True)
            change(self.template if self.number>=2 else 'unchanged',path/'declared-change.json')
            # Synchronise the exact current selector and target template, including prior repairs.
            primary_web=v.find(self.observations[observation['evidence_id']],'deployments','web')
            current_web=rb.kjson('get','deployment','web')
            if translate(primary_web['spec'])!=current_web['spec']:
                raise RuntimeError('Replica target spec differs after declared change')
            fault_selector=primary_before['services']['web']['spec']['selector']
            rb.kubectl('patch','service','web','--type=json','-p',json.dumps([{'op':'replace','path':'/spec/selector','value':fault_selector}]))
            replica_specs=strict_application('k3d-lab-validation',REPLICA_BASE)
            vp,vr=volumes('k3d-lab'),volumes('k3d-lab-validation')
            roots=docker_roots()
            separate=set(vp)==set(vr) and all(vp[n]['claim_uid']!=vr[n]['claim_uid'] and vp[n]['pv_uid']!=vr[n]['pv_uid'] for n in vp) and len({x['storage_volume'] for x in roots.values()})==6
            target=v.target(self.row); nt=v.non_target(self.row)
        with context('k3d-lab'):
            after=strict_application('k3d-lab',BASE); current=supported.collect('P5',self.contract)
        passed=translate(primary_before)==replica_specs and separate and not target['healthy'] and nt['passed'] and primary_before==after and current['relevant_revision']==observation['revision']
        proof={'at':time.time(),'fidelity_passed':passed,'primary_specs':primary_before,'replica_specs':replica_specs,
            'primary_volumes':vp,'replica_volumes':vr,'writable_storage_separate':separate,
            'storage_roots':roots,
            'primary_before':digest(primary_before),'primary_after':digest(after),'isolated':separate,
            'source_revision':observation['revision'],'source_contract':observation['contract'],
            'replica_fault_observed':not target['healthy'],'replica_non_target_passed':nt['passed'],
            'scope':'Exact application configurations, current target/Service links and workload; independent seeds and retained cart copied. Background transaction histories are not identical snapshots.'}
        save(path/'fidelity.json',proof)
        self.replica_path=path
        return proof
    def isolated_replay(self, plan, replica, remaining):
        with context('k3d-lab-validation'):
            self.isolated_before_action=v.non_target(self.row)
            if not self.isolated_before_action['passed']:raise RuntimeError('Replica pre-action checks failed')
            execution=self.run_action(plan,self.replica_path,'k3d-lab-validation',min(120,remaining))
            result=self.verify_action(plan,execution,self.replica_path,'k3d-lab-validation',remaining)
        result['cleanup_confirmed']=execution['runner_termination_confirmed']
        result['cleanup_definition']='Quiescent isolated runner; replica remains leased. Next replay charges a full restore. No primary reset.'
        save(self.replica_path/'replay-result.json',result)
        return result
    def audit_certificate(self, certificate, remaining):
        path=ROOT/certificate['evidence_id']
        saved=json.loads(path.read_text())
        proof=json.loads((self.replica_path/'fidelity.json').read_text())
        return saved['outcome']==certificate['outcome'] and proof['fidelity_passed'] and saved['outcome_fixed_at']==certificate['outcome_fixed_at']
    def snapshot(self):
        with context('k3d-lab'): specs=strict_application('k3d-lab',BASE)
        save(self.path/'state-after.json',specs)
        return specs
    def publish(self, record):
        save(self.path/'response.json',record)
        self.history.append(record)
        if self.role=='formal':
            progress_path=self.output.parent/'progress.json'
            progress=json.loads(progress_path.read_text())
            progress['responses_completed']+=1
            progress.update(position=progress['responses_completed'],at=time.time(),current_incident=self.number)
            save(progress_path,progress)
        save(self.output/'sequence-progress.json',{'state':'running' if record['recovered'] else 'unresolved',
            'completed_incidents':len(self.history),'planned_incidents':self.protocol['incidents_per_sequence'],
            'role':self.role,'records':[str((self.output/f'incident-{i+1:02d}/response.json').relative_to(ROOT)) for i in range(len(self.history))]})


def run_sequence(output,protocol,item,role,count):
    port=Port(output,protocol,item['template'],role)
    engine=Engine(port,time.time,Limits(protocol['response_cap_seconds'],30,1,1,1,6),
                  Plan('P5',(ROOT/'candidates/P5.yaml').read_text(),'historical'))
    with context('k3d-lab'):
        result=engine.run_sequence(item['process'],item['name'],digest({'manifest':sha(BASE),'seed':'fixed'}),
            [{'id':str(i+1),'healthy_interval_seconds':0} for i in range(count)])
    save(output/'sequence.json',result)
    restore('k3d-lab',output/'between-sequence-reset.log')
    if hasattr(port,'replica_path'):restore('k3d-lab-validation',output/'between-sequence-replica-reset.log')
    save(output/'cleanup.json',{'at':time.time(),'primary_baseline_confirmed':True,'isolated_replica_released':True})
    return result


def load_retained_sequence(protocol,item,role):
    """Reference complete compatible evidence in place; never relabel old attempts."""
    source=(ROOT/item['source']).resolve()
    if not source.is_relative_to(ROOT/'results'):raise ValueError('Sequence source outside results')
    previous=json.loads((source/'protocol.json').read_text())
    ignored={'version','written_at','pilot_resume','formal_resume'}
    if {k:v for k,v in previous.items() if k not in ignored}!={k:v for k,v in protocol.items() if k not in ignored}:
        raise ValueError('Sequence experiment settings changed; cannot carry evidence')
    frozen=json.loads((source/'freeze.json').read_text())
    if sha(source/'protocol.json')!=frozen['protocol_sha256']:raise ValueError('Prior pilot protocol changed')
    allowed={'tools/rq3b_live.py','offline_work/rq3b_live_v1/test_live.py'}
    for name,value in frozen['sources'].items():
        if sha(source/'source-snapshot'/name)!=value:raise ValueError('Prior frozen source changed')
        if name not in allowed and sha(ROOT/name)!=value:raise ValueError('Pilot dependency changed: '+name)
    def components(path):
        tree=ast.parse(path.read_text())
        return {n.name:ast.dump(n,include_attributes=False) for n in tree.body
                if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name not in
                {'restore','load_retained_sequence','load_pilot_resume','load_formal_resume',
                 'accept_pilots','run','check_plan'}}
    if components(source/'source-snapshot/tools/rq3b_live.py')!=components(ROOT/'tools/rq3b_live.py'):
        raise ValueError('Pilot physical incident/response adapters changed; repeat the pilot')
    for name,value in item['evidence_sha256'].items():
        if sha(source/name)!=value:raise ValueError('Prior pilot evidence changed: '+name)
    directory=source/item.get('sequence','pilot-proposed')
    result=json.loads((directory/'sequence.json').read_text())
    if result['unresolved_incidents'] or result['unpresented_incidents'] or len(result['records'])!=result['planned_incidents']:
        raise ValueError('Only complete resolved sequences can be retained')
    if role=='formal' and (result['formal_trials']!=5 or len(result['records'])!=5):raise ValueError('Five formal responses required')
    if role=='pilot' and result['formal_trials']!=0:raise ValueError('A pilot cannot contain formal responses')
    for record in result['records']:
        if not record['recovered']:raise ValueError('Unresolved prior pilot')
        for attempt in record['live_attempts']:
            evidence=ROOT/attempt['evidence_id']
            execution=json.loads((evidence.parent/'execution.json').read_text())
            if not execution['runner_termination_confirmed']:raise ValueError('Prior pilot runner not quiescent')
            content=(evidence.parent/'plan.yaml').read_text()
            plan=Plan('retained',content,attempt['plan_origin'])
            if plan.sha256!=attempt['candidate_sha256']:raise ValueError('Prior executed plan identity differs')
            check_plan(plan,'k3d-lab')
        for attempt in record['isolated_attempts']:
            if not attempt.get('cleanup_confirmed'):raise ValueError('Prior isolated runner not quiescent')
            evidence=ROOT/attempt['evidence_id']
            check_plan(Plan('retained',(evidence.parent/'plan.yaml').read_text(),'historical'),'k3d-lab-validation')
    result['_evidence_directory']=str(directory.relative_to(ROOT))
    return result


def load_pilot_resume(protocol):
    item=protocol['pilot_resume']
    return [load_retained_sequence(protocol,x,'pilot') for x in item.get('sequences',[item])]


def load_formal_resume(protocol):
    item=protocol.get('formal_resume',{})
    results=[load_retained_sequence(protocol,x,'formal') for x in item.get('sequences',[])]
    if [r['sequence_id'] for r in results]!=[x['name'] for x in protocol['order'][:len(results)]]:
        raise ValueError('Retained formal sequences must be a complete ordered prefix')
    return results


def accept_pilots(results, output):
    """Check actual routes and raw traces; recovery alone cannot accept adapters."""
    by_process={r['process']:r for r in results}
    if set(by_process)!={'proposed','replanning','full_revalidation'}:raise RuntimeError('Three pilot processes required')
    proposed=by_process['proposed']['records'];full=by_process['full_revalidation']['records'];replanned=by_process['replanning']['records']
    if [len(proposed),len(full),len(replanned)]!=[2,2,1]:raise RuntimeError('Five pilot incidents required')
    for result in results:
        if result['unresolved_incidents'] or result['unpresented_incidents']:raise RuntimeError('Incomplete live pilot')
        directory=ROOT/result['_evidence_directory'] if '_evidence_directory' in result else output/result['sequence_id']
        expected=json.loads((directory/'starting-state.json').read_text())['preserved_cart']
        for index,record in enumerate(result['records'],1):
            presentation=json.loads((directory/f'incident-{index:02d}/presentation.json').read_text())
            if presentation['pre_marker']!=expected:raise RuntimeError('Pilot state retention not proved')
            for attempt in record['live_attempts']:
                if attempt.get('executed') is not True or attempt['outcome']!='Successful repair':raise RuntimeError('Actual successful pilot action required')
                raw=json.loads((ROOT/attempt['evidence_id']).read_text())
                if not all(raw.get(k) is True for k in ('target_recovered','non_target_passed','window_complete')) or raw.get('preserved_cart')!=expected:
                    raise RuntimeError('Pilot current recovery or data trace unavailable')
    if proposed[0]['live_attempts'][0]['route']!='direct reuse' or proposed[1]['live_attempts'][0]['plan_origin']!='generated':raise RuntimeError('Proposed direct and fallback routes not tested')
    if replanned[0]['live_attempts'][0]['plan_origin']!='generated':raise RuntimeError('Replanning route not tested')
    for index,outcome in enumerate(('Successful repair','Confirmed failure')):
        attempts=full[index]['isolated_attempts']
        if len(attempts)!=1 or attempts[0].get('executed') is not True or attempts[0]['outcome']!=outcome or attempts[0].get('cleanup_confirmed') is not True:
            raise RuntimeError('Actual isolated success/failure and quiescent boundary not tested')
        full_directory=ROOT/by_process['full_revalidation']['_evidence_directory'] if '_evidence_directory' in by_process['full_revalidation'] else output/'pilot-full'
        proof=json.loads((full_directory/f'incident-{index+1:02d}/isolated/fidelity.json').read_text())
        if not proof['fidelity_passed']:raise RuntimeError('Pilot replica fidelity not proved')
    if full[0]['live_attempts'][0]['plan_origin']!='historical' or full[1]['live_attempts'][0]['plan_origin']!='generated':raise RuntimeError('Full revalidation live and fallback routes not tested')
    return {'at':time.time(),'passed':True,'pilot_incidents':5,'source_freeze_sha256':sha(output/'freeze.json'),
        'carried_pilot_evidence':[r['_evidence_directory'] for r in results if '_evidence_directory' in r],
        'processes':list(by_process),'fresh_generated_execution_verified':True,
        'within_sequence_state_retained':True,'isolated_success_and_failure_tested':True,'shared_fallback_verified':True}


def run(output):
    protocol=json.loads((output/'protocol.json').read_text())
    freeze=json.loads((output/'freeze.json').read_text())
    if sha(output/'protocol.json')!=freeze['protocol_sha256']:raise RuntimeError('Frozen protocol changed')
    for name,value in freeze['sources'].items():
        if sha(ROOT/name)!=value:raise RuntimeError('Frozen source changed: '+name)
    progress={'state':'pilot','at':time.time(),'position':0,'target':45,'responses_completed':0,'formal_sequences_completed':0,'pilot_sequences_completed':0,'pilot_responses_completed':0,'pilot_target':5}
    save(output/'progress.json',progress)
    try:
        pilots=[{'process':'proposed','template':'invalidating','name':'pilot-proposed','count':2},
                {'process':'replanning','template':'unchanged','name':'pilot-replanning','count':1},
                {'process':'full_revalidation','template':'invalidating','name':'pilot-full','count':2}]
        results=[]
        if protocol.get('pilot_resume'):
            carried=load_pilot_resume(protocol)
            count=sum(len(r['records']) for r in carried)
            progress.update(current_sequence='prior-pilot-reset',pilot_responses_completed=count,at=time.time())
            save(output/'progress.json',progress)
            restore('k3d-lab',output/'prior-pilot-baseline-reset.log')
            save(output/'pilot-resume-acceptance.json',{'at':time.time(),'passed':True,
                 'sources':[r['_evidence_directory'] for r in carried],'pilot_responses_carried':count,
                 'formal_responses_carried':0,'incident_and_response_adapters_unchanged':True,
                 'between_sequence_baseline_confirmed':True})
            results.extend(carried)
            carried_names={r['sequence_id'] for r in carried}
            pilots=[p for p in pilots if p['name'] not in carried_names]
            progress['pilot_sequences_completed']=len(carried)
        for item in pilots:
            progress.update(current_sequence=item['name'],at=time.time());save(output/'progress.json',progress)
            result=run_sequence(output/item['name'],protocol,item,'pilot',item['count']);results.append(result)
            if result['unresolved_incidents'] or result['unpresented_incidents']:
                raise RuntimeError('Live pilot unresolved; formal collection not started')
            progress['pilot_sequences_completed']+=1
            progress['pilot_responses_completed']=sum(len(r['records']) for r in results)
            save(output/'progress.json',progress)
        gate=accept_pilots(results,output)
        save(output/'pilot-acceptance.json',gate)
        sequences=load_formal_resume(protocol)
        progress.update(state='running',responses_completed=sum(len(r['records']) for r in sequences),
                        formal_sequences_completed=len(sequences),position=sum(len(r['records']) for r in sequences),
                        archived_instrumentation_attempts=len(protocol.get('formal_resume',{}).get('instrumentation_attempts',[])))
        save(output/'progress.json',progress)
        save(output/'retained-sequences.json',{'at':time.time(),'sequences':sequences,
            'instrumentation_attempts':protocol.get('formal_resume',{}).get('instrumentation_attempts',[])})
        for item in protocol['order'][len(sequences):]:
            progress.update(current_sequence=item['name'],position=progress['responses_completed']+1,at=time.time());save(output/'progress.json',progress)
            result=run_sequence(output/item['name'],protocol,item,'formal',5);sequences.append(result)
            progress['responses_completed']=sum(len(r['records']) for r in sequences);progress['formal_sequences_completed']+=1
            progress['unpresented_incidents']=sum(r['unpresented_incidents'] for r in sequences)
            progress['position']=progress['responses_completed'];save(output/'progress.json',progress)
            # An unresolved sequence is evidence. Later matched sequences may start only
            # after the registered between-sequence restore has passed.
        progress.update(state='complete',at=time.time(),sequences_complete=9)
        save(output/'progress.json',progress)
        save(output/'completion.json',{'at':time.time(),'registered_slots':45,'presented_incidents':progress['responses_completed'],
            'unpresented_incidents':progress.get('unpresented_incidents',0),'sequences':len(sequences),
            'successful_incidents':sum(r['recovered_incidents'] for r in sequences),
            'unresolved_incidents':sum(r['unresolved_incidents'] for r in sequences),
            'archived_instrumentation_attempts':protocol.get('formal_resume',{}).get('instrumentation_attempts',[])})
    except Exception as error:
        progress.update(json.loads((output/'progress.json').read_text()))
        progress.update(state='halted',at=time.time(),error=str(error));save(output/'progress.json',progress)
        (output/'error.log').write_text(traceback.format_exc());raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output.resolve())

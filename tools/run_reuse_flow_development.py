"""Consume decisions in the existing incident; separate replica and live labels."""
import argparse
import copy
import json
import subprocess
import time
import traceback
from pathlib import Path

import audit_main_development as audit
import main_verifier as v
import registration_trials as reg
import reset_baseline as rb
import reuse_assessor as a
import reuse_evidence as evidence
import reuse_flow_executor as ex
import reuse_workflow_v2 as workflow
import scenario_feasibility as s
import supported_reuse_v2 as supported
from reuse_validation import Adapter


def select(p):
    rb.CONTEXT=p['context'];rb.NS=p['namespace'];rb.MANIFEST=ex.ROOT/p['manifest']
    rb.FINGERPRINT=rb.MANIFEST.with_suffix('.fingerprint.json')


def confirm_environment(p, context=None):
    profile=p.get('environment_profile')
    if not profile:return {'declared':False,'passed':True}
    context=context or p['context'];checks=[]
    def get(*args):
        return subprocess.run(['kubectl','--context',context,'-n',p['namespace'],
            '--request-timeout=8s',*args],capture_output=True,text=True,timeout=12)
    for name in profile['deployments']:
        deployment=get('get','deployment',name,'-o','json')
        pods=get('get','pods','-l','service='+name,'-o','json')
        d=json.loads(deployment.stdout) if deployment.returncode==0 else {}
        items=json.loads(pods.stdout).get('items',[]) if pods.returncode==0 else []
        expected=profile['dnsConfig'];option='single-request-reopen'
        active=[x for x in items if not x['metadata'].get('deletionTimestamp')]
        pod_checks=[]
        for pod in active:
            observed=get('exec',pod['metadata']['name'],'--','cat','/etc/resolv.conf')
            # --request-timeout is a kubectl option, not an exec payload argument.
            pod_checks.append({'pod':pod['metadata']['name'],'uid':pod['metadata']['uid'],
                'dnsConfig':pod['spec'].get('dnsConfig'),'resolv_conf':observed.stdout,
                'passed':pod['spec'].get('dnsConfig')==expected and observed.returncode==0
                    and option in observed.stdout.split()})
        checks.append({'deployment':name,'declared_dns_config_matches':
            d.get('spec',{}).get('template',{}).get('spec',{}).get('dnsConfig')==expected,
            'pods':pod_checks,'passed':bool(active) and all(x['passed'] for x in pod_checks)
                and d.get('spec',{}).get('template',{}).get('spec',{}).get('dnsConfig')==expected})
    return {'at':time.time(),'declared':True,'context':context,'profile':profile,
            'checks':checks,'passed':all(x['passed'] for x in checks)}


def setup(row,p,path,current):
    reg.reset_s4(path/'initial-reset.log',p);rb.ensure_probe()
    r={'initial_reset_log':str(path/'initial-reset.log'),'setup_checks':[], 'healthy_window':[],
       'setup_phase':'apply healthy variant'}
    ex.save(path/'incident-setup.json',r)
    r['replacement']=ex.apply_variant(row,ex.ROOT/row['healthy_manifest'])
    r['environment_check']=confirm_environment(p)
    ex.save(path/'incident-setup.json',r)
    if not r['environment_check']['passed']:raise RuntimeError('Declared resolver environment not applied')
    r['setup_phase']='healthy construction checks';ex.save(path/'incident-setup.json',r)
    end=time.monotonic()+90
    while True:
        health=s.healthy(row,ex.ROOT/row['healthy_manifest']);r['setup_checks'].append(health)
        ex.save(path/'incident-setup.json',r)
        if health['passed']:break
        if time.monotonic()>=end:raise RuntimeError('Healthy variant setup rejected')
        time.sleep(5)
    start=time.monotonic()
    r['setup_phase']='healthy observation window';ex.save(path/'incident-setup.json',r)
    while True:
        target=v.target(row)
        r['latest_target_before_non_target']=target;ex.save(path/'incident-setup.json',r)
        nt=v.non_target(row)
        r['healthy_window'].append({'target':target,'non_target':nt})
        ex.save(path/'incident-setup.json',r)
        if not target['healthy'] or not nt['passed']:raise RuntimeError('Healthy gate rejected')
        if time.monotonic()-start>=p['timing']['healthy_window']:break
        time.sleep(5)
    r['setup_phase']='read-only incident evidence';ex.save(path/'incident-setup.json',r)
    r['healthy_snapshot']=v.collect()
    r['cached_read_only_observation']=supported.collect(row['candidate'],current)
    r['fault_activation_at']=time.time();r['injection']=ex.inject_current(row)
    r['setup_phase']='fault confirmation';ex.save(path/'incident-setup.json',r)
    end=time.monotonic()+p['timing']['fault_confirmation'];r['fault_confirmation']=[]
    while True:
        target=v.target(row);r['fault_confirmation'].append(target)
        ex.save(path/'incident-setup.json',r)
        if not target['healthy']:break
        if time.monotonic()>=end:raise RuntimeError('Intended fault not observed')
        time.sleep(3)
    start=time.monotonic();r['fault_persistence']=[];r['setup_phase']='fault persistence window'
    while True:
        target=v.target(row);nt=v.non_target(row)
        r['fault_persistence'].append({'at':time.time(),'elapsed_seconds':time.monotonic()-start,
                                     'target':target,'non_target':nt})
        ex.save(path/'incident-setup.json',r)
        if target['healthy'] or not nt['passed']:raise RuntimeError('Fault self-resolved or pre-existing non-target instability')
        if time.monotonic()-start>=60:break
        time.sleep(5)
    return r


def preserve_failed_setup_logs(path):
    """Read logs before reset destroys failed setup Pods; never retry a check here."""
    diagnostics=path/'failed-setup-diagnostics';diagnostics.mkdir(exist_ok=True)
    statuses=[]
    for name in ('web','cart','catalogue'):
        args=['kubectl','--context',rb.CONTEXT,'-n',rb.NS,'logs','deployment/'+name,
              '--since=3m','--tail=600','--request-timeout=5s']
        try:
            process=subprocess.run(args,capture_output=True,text=True,timeout=8)
            (diagnostics/(name+'.log')).write_text(process.stdout)
            statuses.append({'deployment':name,'returncode':process.returncode,'stderr':process.stderr[:500]})
        except subprocess.SubprocessError as error:
            statuses.append({'deployment':name,'error':str(error)})
    ex.save(diagnostics/'receipt.json',{'at':time.time(),'read_only_after_rejected_setup':True,
        'checks_not_retried_or_reclassified':True,'statuses':statuses})


def run_one(out,case):
    directory=out/case['name'];path=directory/'primary';p=ex.inputs(path);select(p)
    row=p['scenarios'][0];key=row['candidate'];current=a.contract(row,
        str((path/'protocol.json').relative_to(ex.ROOT)),ex.sha(path/'protocol.json'))
    result={'type':'live_reuse_workflow_development','case':case['name'],'candidate':key,
        'main_trials':0,'started_at':time.time(),'passed':False,'primary_post_reset_confirmed':False}
    adapter=None;prepared=None
    try:
        print('START '+case['name']+' healthy/reset/fault order',flush=True)
        prepared=setup(row,p,path,current)
        adapter=Adapter(directory,p,row,prepared['injection']);observations=[]
        history=json.loads((ex.ROOT/'candidates/registration.v1.json').read_text())
        def collect(candidate,contract):
            if case['use_genuinely_aged_cache'] and not observations:
                observed=copy.deepcopy(prepared['cached_read_only_observation'])
                if time.time()-observed['started_at']<=case['configuration']['observation_ttl']:
                    raise RuntimeError('Declared stale-cache case was not actually stale')
            else:observed=supported.collect(candidate,contract)
            observations.append(copy.deepcopy(observed))
            return observed
        def gate(candidate,observed):
            return evidence.common_safety_gate(candidate,ex.ROOT/'candidates'/(candidate+'.yaml'),
                p['candidate_hashes'][candidate],rb.CONTEXT,rb.NS,observed,sorted(rb.ALL_WORKLOADS))
        assessed=workflow.evaluate(key,p['candidate_hashes'][key],current,history,collect,gate,
            config=case['configuration'],validate=adapter.validate,compare=supported.compare)
        ex.save(path/'observations.json',observations);workflow.save_new(path/'workflow.json',assessed)
        result['workflow']=assessed
        print(case['name']+' decision: '+str(assessed['initial_decision'])+' -> '+str(assessed['final_decision'])+
              ', permission='+str(assessed['execution_permitted']),flush=True)
        if assessed['execution_permitted']:
            def boundary(record,observed,contract):
                states=supported.compare(key,p['candidate_hashes'][key],observed,current,
                    ttl=case['configuration']['observation_ttl'],validation=assessed['validation_certificate'])
                direct=a.decision(states,0)['decision']=='Direct reuse'
                same=observed['relevant_revision']==assessed['relevant_revision']
                timely=time.time()<=assessed['permission_expires_at']
                expiry=(key!='P1' or time.time()-prepared['fault_activation_at']+p['timing']['outcome_window']<1200)
                return {'passed':direct and same and timely and expiry,'states':states,
                    'permission_valid_until':assessed['permission_expires_at'],
                    'same_revision':same,'fresh_permission':timely,'finished_before_fault_expiry_budget':expiry,
                    'workflow_record_sha256':ex.sha(path/'workflow.json'),'at':time.time()}
            live=ex.run_case(row,'repair',path/'live-repair',p,existing=prepared,before_launch=boundary)
            result['live_outcome']=live['repair_outcome']
            result['primary_post_reset_confirmed']=live.get('post_reset_confirmed') is True
            result['live_executor_audit']=audit.audit(path/'live-repair/record.json')
            if not result['live_executor_audit']['accepted']:
                raise RuntimeError('Live execution trace rejected: '+str(result['live_executor_audit']['failures']))
        else:
            result['live_outcome']='Not executed'
            end=v.target(row);result['incident_still_faulty_at_decline']=not end['healthy']
            if end['healthy']:raise RuntimeError('Fault unexpectedly self-resolved during decision')
            reg.reset_s4(path/'declined-post-reset.log',p);result['primary_post_reset_confirmed']=True
        result['replica_cleanup']=adapter.finish()
        if case['expected_timeout'] and not result['replica_cleanup']['passed']:
            record_path=directory/'replica/repair/record.json'
            r=json.loads(record_path.read_text())
            safe=(r.get('post_reset_confirmed') is True and r.get('action_executed') is False
                and (directory/'replica/cancel-requested.json').exists() and not (directory/'replica/certificate.json').exists()
                and 'baseline confirmed' in (directory/'replica/repair/post-reset.log').read_text())
            result['controlled_timeout_reset_accepted']=safe
            if not safe:raise RuntimeError('Timeout replica did not terminate/reset safely')
        elif not result['replica_cleanup']['passed']:
            raise RuntimeError('Replica trace or reset not accepted')
        result['rule_acceptance']=(assessed['initial_decision']==case['initial_decision']
            and assessed['execution_permitted'] is case['execution_permitted'] and assessed['history_preserved'])
        if case['expected_validation_or_live_outcome']:
            actual=(assessed.get('validation_certificate') or {}).get('outcome',result['live_outcome'])
            result['outcome_acceptance']=actual==case['expected_validation_or_live_outcome']
            if assessed['execution_permitted']:result['outcome_acceptance'] &= result['live_outcome']=='Successful repair'
        else:result['outcome_acceptance']=result['live_outcome']=='Not executed'
        result['passed']=result['rule_acceptance'] and result['outcome_acceptance']
    except Exception as error:
        result.update(error=str(error),traceback=traceback.format_exc())
        if prepared is None:
            try:preserve_failed_setup_logs(path)
            except Exception as diagnostic_error:result['setup_diagnostic_error']=str(diagnostic_error)
    finally:
        # Never reset a live candidate while its runner is active; the executor
        # owns cancellation/reset. Setup failures have no candidate runner.
        if not result['primary_post_reset_confirmed']:
            try:
                reg.reset_s4(path/'rejection-post-reset.log',p);result['primary_post_reset_confirmed']=True
            except Exception as error:result['primary_reset_error']=str(error)
        if adapter and adapter.child and 'replica_cleanup' not in result:
            try:result['replica_cleanup']=adapter.finish()
            except Exception as error:result['replica_cleanup_error']=str(error)
        if p.get('environment_profile'):
            try:
                result['primary_environment_after_reset']=confirm_environment(p)
                if adapter and adapter.child:
                    result['replica_environment_after_reset']=confirm_environment(p,'k3d-lab-validation')
                result['passed'] &= all(check['passed'] for field,check in result.items()
                    if field.endswith('environment_after_reset'))
            except Exception as error:
                result['passed']=False;result['environment_reset_error']=str(error)
        result['completed_at']=time.time();ex.save(directory/'acceptance.json',result)
        print('FINISH '+case['name']+' passed='+str(result['passed'])+' '+str(result.get('error','')),flush=True)
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--case')
    args=ap.parse_args();out=args.output.resolve();plan=json.loads((out/'plan.json').read_text())
    for name,h in plan['source_hashes'].items():
        if ex.sha(ex.ROOT/'tools'/name)!=h:raise RuntimeError('Source changed: '+name)
    for case in plan['cases']:
        if args.case and case['name']!=args.case:continue
        if case.get('carried_from_directory'):
            source=ex.ROOT/case['carried_from_directory']/'acceptance.json'
            if ex.sha(source)!=case['carried_acceptance_sha256'] or not json.loads(source.read_text())['passed']:
                raise RuntimeError('Carried acceptance changed or was not accepted')
            print('CARRY immutable accepted '+case['name'],flush=True)
            continue
        if (out/case['name']/'acceptance.json').exists():
            old=json.loads((out/case['name']/'acceptance.json').read_text())
            if not old['passed']:raise RuntimeError('Preserve rejected attempt; new prospective retry required')
            continue
        result=run_one(out,case)
        if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()

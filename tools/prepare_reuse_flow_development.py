"""Freeze development acceptance cases and decision/execution boundaries first."""
import argparse
import copy
import json
import time
from pathlib import Path

import yaml
import main_executor_v4 as previous
import reuse_flow_executor as ex
import reset_baseline as rb
from prepare_replica_development import translate

CONFIG = {'observation_ttl':30,'refresh_budget':1,'replay_budget':1,
          'total_budget_seconds':600,'max_assessments':6}
CASES = [
    ('01-direct-P5','P5-original-conditions','Direct reuse',True,'Successful repair'),
    ('02-revalidate-P2','P2-2-1','Revalidate',True,'Successful repair'),
    ('03-failed-validation-P3','P3-2-2','Revalidate',False,'Confirmed failure'),
    ('04-revalidate-P1','P1-4-2','Revalidate',True,'Successful repair'),
    ('05-revalidate-P3','P3-4-2','Revalidate',True,'Successful repair'),
    ('06-revalidate-P4','P4-4-2','Revalidate',True,'Successful repair'),
    ('07-revalidate-P5','P5-4-2','Revalidate',True,'Successful repair'),
    ('08-contradicted-P2','DEV-P2-minimum4','Stop reuse',False,None),
    ('09-refresh-stale-P2','P2-original-conditions','Revalidate',True,'Successful repair'),
    ('10-budget-exhausted-P2','P2-2-1','Stop reuse',False,None),
    ('11-validation-timeout-P2','P2-2-1','Revalidate',False,None),
]
SOURCES = list(dict.fromkeys(ex.SOURCES+[
    'prepare_reuse_flow_development.py','run_reuse_flow_development.py',
    'reuse_validation.py','reuse_workflow_v2.py','supported_reuse_v2.py','resource_quantities.py',
    'audit_main_development.py','test_reuse_flow.py','audit_reuse_flow.py',
    'reuse_flow_executor.py','run_replica_development.py','prepare_replica_development.py']))
ORCHESTRATION_ONLY = {'prepare_reuse_flow_development.py','run_reuse_flow_development.py',
                      'audit_reuse_flow.py','test_reuse_flow.py'}
RESOLVER_PROFILE = {'name':'glibc-parallel-query-compatibility-v1',
    'deployments':['cart','catalogue','user'],
    'dnsConfig':{'options':[{'name':'single-request-reopen'}]},
    'evidence':'results/reuse-flow-cart-network-diagnostic-v1/acceptance.json'}


def resolver_profile(docs, enabled):
    """A prospective common baseline setting, never a candidate repair."""
    result=copy.deepcopy(docs)
    if enabled:
        for obj in result:
            if obj and obj.get('kind')=='Deployment' and obj['metadata']['name'] in RESOLVER_PROFILE['deployments']:
                obj['spec']['template']['spec']['dnsConfig']=copy.deepcopy(RESOLVER_PROFILE['dnsConfig'])
    return result


def can_carry(directory, case, prior_plan):
    previous=next((c for c in prior_plan['cases'] if c['name']==case['name']),None)
    if previous is None:return False
    if previous.get('carried_from_directory'):
        directory=ex.ROOT/previous['carried_from_directory']
        if ex.sha(directory/'acceptance.json')!=previous['carried_acceptance_sha256']:
            raise RuntimeError('Previously carried acceptance changed')
    receipt=directory/'acceptance.json'
    if not receipt.exists() or not json.loads(receipt.read_text()).get('passed'):
        return False
    fields=('scenario','initial_decision','execution_permitted','expected_validation_or_live_outcome',
            'configuration','use_genuinely_aged_cache','expected_timeout','environment_profile')
    if any(case.get(f)!=previous.get(f) for f in fields):return False
    # Adding a new runtime module is a method change too, even if all old files
    # remain byte-identical. Do not silently carry receipts across that change.
    if set(SOURCES)-ORCHESTRATION_ONLY != set(prior_plan['source_hashes'])-ORCHESTRATION_ONLY:
        return False
    return all(ex.sha(ex.ROOT/'tools'/name)==h for name,h in prior_plan['source_hashes'].items()
               if name not in ORCHESTRATION_ONLY)


def rejected_boundaries(candidate):
    """A failed pre-validation setup has no replica runner to reset.

    Any request/worker/action artifact makes a full replica reset mandatory.
    Keep the distinction explicit, rather than manufacturing an unused reset.
    """
    r=json.loads(candidate.read_text())
    if not r.get('primary_post_reset_confirmed'):
        raise RuntimeError('Rejected primary boundary is not reset')
    replica=candidate.parent/'replica'
    used=any((replica/name).exists() for name in
             ('request.json','worker.log','repair','certificate.json','outcome-record.json'))
    rr=replica/'repair/record.json'
    if used and (not rr.exists() or not json.loads(rr.read_text()).get('post_reset_confirmed')):
        raise RuntimeError('Rejected replica boundary is not reset')
    return {'receipt':str(candidate.relative_to(ex.ROOT)),
            'receipt_sha256':ex.sha(candidate),'primary_reset_confirmed':True,
            'replica_boundary':'reset confirmed' if used else 'unused; no request, worker or action artifacts'}


def prepare(out, resume_from=None, resolver_compatibility=False):
    # runtime startup files can exist; a second prospective plan cannot.
    if (out/'plan.json').exists():raise RuntimeError('Preserve previous prospective plan')
    out.mkdir(exist_ok=True)
    prior_plan=None;prior_rejections=[]
    if resume_from:
        prior_plan=json.loads((resume_from/'plan.json').read_text())
        for candidate in resume_from.glob('*/acceptance.json'):
            r=json.loads(candidate.read_text())
            if not r.get('passed'):
                prior_rejections.append(rejected_boundaries(candidate))
    retry=ex.ROOT/'results/reuse-flow-development-v1/retry-decision.json'
    if out.name.endswith('v2'):
        if not retry.exists() or not json.loads(retry.read_text())['both_reset_boundaries_confirmed']:
            raise RuntimeError('Prospective retry requires preserved rejection and both reset boundaries')
    parent=ex.ROOT/'results/pre-main-development-v4';old=previous.inputs(parent)
    docs=[d for d in yaml.safe_load_all((ex.ROOT/old['manifest']).read_text()) if d]
    profile=copy.deepcopy(RESOLVER_PROFILE) if resolver_compatibility else None
    if profile:
        diagnostic=ex.ROOT/profile['evidence']
        if not diagnostic.exists() or not json.loads(diagnostic.read_text()).get('accepted'):
            raise RuntimeError('Resolver compatibility requires an accepted diagnostic')
        profile['evidence_sha256']=ex.sha(diagnostic)
        docs=resolver_profile(docs,True)
    rows=old['scenarios']+old['controls'];plans=[]
    for name,identity,initial,permit,outcome in CASES:
        case=out/name;case.mkdir()
        row=copy.deepcopy(next(x for x in rows if x['id']==identity))
        config=copy.deepcopy(CONFIG)
        if name.startswith('10-'):config.update(refresh_budget=0,replay_budget=0)
        if name.startswith('11-'):config.update(refresh_budget=0,total_budget_seconds=5)
        for role,context in [('primary','k3d-lab'),('replica','k3d-lab-validation')]:
            directory=case/role;directory.mkdir()
            mapped_docs=translate(docs) if role=='replica' else docs
            manifest=directory/'baseline.yaml'
            manifest.write_text(yaml.safe_dump_all(mapped_docs,sort_keys=False,width=4096))
            rb.MANIFEST=manifest
            ex.save(manifest.with_suffix('.fingerprint.json'),rb.declared_fingerprint())
            selected=copy.deepcopy(row)
            healthy=resolver_profile(list(yaml.safe_load_all((ex.ROOT/row['healthy_manifest']).read_text())),resolver_compatibility)
            hp=directory/'healthy.yaml'
            hp.write_text(yaml.safe_dump_all(translate(healthy) if role=='replica' else healthy,sort_keys=False,width=4096))
            selected.update(healthy_manifest=str(hp.relative_to(ex.ROOT)),manifest_sha256=ex.sha(hp))
            protocol=copy.deepcopy(old)
            protocol.update(version=out.name+'-'+role,context=context,environment_profile=profile,
                written_at=time.time(),manifest=str(manifest.relative_to(ex.ROOT)),manifest_sha256=ex.sha(manifest),
                scenarios=[selected],controls=[],main_trials=0,main_protocol_ready=False,
                parent_protocol=str((parent/'protocol.json').relative_to(ex.ROOT)),parent_protocol_sha256=ex.sha(parent/'protocol.json'))
            protocol['timing']['pre_action']=0 if role=='primary' else 60
            protocol['decision_workflow_configuration']=config
            protocol['timing_policy']='Primary incident has a separately recorded 60s fault-persistence window before assessment; live action consumes permission immediately. Replica has its own 60s pre-action period and 210s outcome window. Cleanup is after outcome publication and is separately accepted.'
            ex.save(directory/'protocol.json',protocol)
            previous_sources=ex.SOURCES
            try:
                ex.SOURCES=SOURCES;ex.freeze(directory)
            finally:ex.SOURCES=previous_sources
        selected_case={'name':name,'scenario':identity,'initial_decision':initial,'execution_permitted':permit,
            'development_rule_expectation_only':True,'actual_outcome_is_independent':True,
            'expected_validation_or_live_outcome':outcome,'configuration':config,
            'use_genuinely_aged_cache':name.startswith('09-'),'expected_timeout':name.startswith('11-'),
            'environment_profile':profile}
        if resume_from and can_carry(resume_from/name,selected_case,prior_plan):
            prior_case=next(c for c in prior_plan['cases'] if c['name']==name)
            original=(ex.ROOT/prior_case['carried_from_directory'] if prior_case.get('carried_from_directory')
                      else resume_from/name)
            selected_case['carried_from_directory']=str(original.relative_to(ex.ROOT))
            selected_case['carried_acceptance_sha256']=ex.sha(original/'acceptance.json')
        plans.append(selected_case)
    archive=out/'tool-snapshot';archive.mkdir()
    for name in SOURCES:(archive/name).write_bytes((ex.ROOT/'tools'/name).read_bytes())
    registration=ex.ROOT/'candidates/registration.v1.json'
    ex.save(out/'plan.json',{'at':time.time(),'version':out.name,'main_trials':0,
        'registered_main_scenarios':0,'main_protocol_ready':False,'cases':plans,'order':[x[0] for x in CASES],
        'source_hashes':{n:ex.sha(ex.ROOT/'tools'/n) for n in SOURCES},
        'environment_profile':profile,
        'prior_plan':str((resume_from/'plan.json').relative_to(ex.ROOT)) if resume_from else None,
        'prior_plan_sha256':ex.sha(resume_from/'plan.json') if resume_from else None,
        'preserved_prior_rejections':prior_rejections,
        'carry_policy':'Only accepted prior cases with identical case definitions and unchanged runtime method/collector/executor sources. Prior immutable receipts remain at their original paths; no old permission is consumed again. Rejected attempts remain, with both resets confirmed before independent retry.',
        'document':str(ex.ROOT/'D-DN-3.1_EN.docx'),'document_sha256':ex.sha(ex.ROOT/'D-DN-3.1_EN.docx'),
        'document_sections':['3.4.1','3.4.2','3.6','3.8.2'],
        'registration_sha256':ex.sha(registration),'candidate_hashes':old['candidate_hashes'],
        'uncertainty_mapping':'Missing/stale automatic observations are refreshed once before spending at most one isolated replay; mapped explicit Unknowns may resolve through current criteria, never a contradicted prerequisite.',
        'certificate_policy':'Immutable actual outcome-phase record and fidelity audit before cleanup. Evidence age starts at the oldest final target/non-target observation, never a cleanup completion timestamp.',
        'fidelity_normalisation':'Exact node-role names, API-assigned Service IPs and undeclared external nodePorts only. All declared service port/targetPort/protocol/selector and full rollout strategies remain compared. Raw external port inventory is retained; clients and verifiers use internal Service DNS.',
        'retry_receipt':str(retry.relative_to(ex.ROOT)) if out.name.endswith('v2') else None,
        'retry_receipt_sha256':ex.sha(retry) if out.name.endswith('v2') else None,
        'expiry_policy':'P1 duration1200s retained. At most600s decision budget,60s prior fault window,210s live outcome; explicit current fault-age/expiry checks reject overlap.',
        'data_policy':'Each development incident and replica begins from the fixed seed; independent fresh cart identifiers. This does not certify data synchronisation for arbitrary accumulated RQ3b histories.',
        'fallback':'Declined reuse is recorded with fallback_required; fixed replanning backend still pending, so no fabricated generated repair or RQ3b result.',
        'scope':'Five admitted families and declared development constructions, including stronger cart/minimum criteria and retained hard anti-affinity. No forty-case replica or RQ3 superiority claim.'})
    print('Prospective flow plan frozen:11 development cases; main remains0.',flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--resume-from',type=Path)
    ap.add_argument('--resolver-compatibility',action='store_true')
    args=ap.parse_args();prepare(args.output.resolve(),args.resume_from.resolve() if args.resume_from else None,args.resolver_compatibility)

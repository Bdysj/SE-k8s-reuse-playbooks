"""Read-only current-cluster acceptance of all five admitted RQ2 collectors.

No candidate action is launched. Workflow execution permission is recorded as
an implementation observation, not a repair outcome or comparative result.
Unresolved evidence remains visible; there is no fabricated replay callback.
"""
import argparse
import json
import time
from pathlib import Path

import main_executor_v4 as ex
import reset_baseline as rb
import reuse_assessor as assessor
import reuse_evidence as evidence
import reuse_workflow as workflow
import supported_reuse as supported


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False)
    parent=ex.ROOT/'results/pre-main-development-v4';p=ex.inputs(parent)
    rb.CONTEXT=p['context'];rb.NS=p['namespace']
    configuration={'observation_ttl':30,'refresh_budget':1,'replay_budget':0,
                   'total_budget_seconds':180,'max_assessments':4}
    names=['accept_live_reuse_observation.py','reuse_workflow.py','reuse_assessor.py',
           'reuse_evidence.py','supported_reuse.py','main_verifier.py']
    archive=out/'tool-snapshot';archive.mkdir()
    for name in names:(archive/name).write_bytes((ex.ROOT/'tools'/name).read_bytes())
    registration=ex.ROOT/'candidates/registration.v1.json'
    history=json.loads(registration.read_text())
    plan={'at':time.time(),'type':'read_only_live_RQ2_observation_development','main_trials':0,
        'candidate_actions':0,'context':rb.CONTEXT,'configuration':configuration,
        'protocol_sha256':ex.sha(parent/'protocol.json'),'registered_history_sha256':ex.sha(registration),
        'source_hashes':{name:ex.sha(ex.ROOT/'tools'/name) for name in names},
        'scope':'Fresh automatic facts and attributed manual contracts for original conditions '
            'in each of five admitted families. No isolated-validation callback, candidate '
            'execution, repair label, comparison accuracy or formal RQ2/RQ3 result.'}
    ex.save(out/'protocol.json',plan);results=[]
    for key in ('P1','P2','P3','P4','P5'):
        row=next(r for r in p['controls'] if r['id']==key+'-original-conditions')
        current=assessor.contract(row,str((parent/'protocol.json').relative_to(ex.ROOT)),ex.sha(parent/'protocol.json'))
        captured=[]
        def collect(candidate,contract):
            observed=supported.collect(candidate,contract);captured.append(observed)
            return observed
        def gate(candidate,observed):
            return evidence.common_safety_gate(candidate,ex.ROOT/'candidates'/(candidate+'.yaml'),
                p['candidate_hashes'][candidate],rb.CONTEXT,rb.NS,observed,sorted(rb.ALL_WORKLOADS))
        result=workflow.evaluate(key,p['candidate_hashes'][key],current,history,collect,gate,
            config=configuration,validate=None,compare=supported.compare)
        result['candidate_action_launched']=False
        ex.save(out/(key+'-observations.json'),captured)
        ex.save(out/(key+'-workflow.json'),result)
        row_result={'candidate':key,'initial_decision':result['initial_decision'],
            'final_decision':result['final_decision'],'execution_permission_only':result['execution_permitted'],
            'candidate_actions':0,'history_preserved':result['history_preserved'],
            'initial_states':result['assessments'][0]['states'],
            'events':result['events'],'workflow_sha256':ex.sha(out/(key+'-workflow.json')),
            'observation_sha256':ex.sha(out/(key+'-observations.json'))}
        results.append(row_result)
        ex.save(out/'acceptance.json',{'at':time.time(),'main_trials':0,'candidate_actions':0,
            'records':results,'complete':len(results)==5,
            'history_unchanged':ex.sha(registration)==plan['registered_history_sha256'],
            'all_original_condition_mappings_supported':len(results)==5 and all(
                r['final_decision']=='Direct reuse' and r['history_preserved'] for r in results)})
        print(json.dumps({'candidate':key,'initial':result['initial_decision'],
              'final':result['final_decision'],'candidate_actions':0}),flush=True)
    if ex.sha(registration)!=plan['registered_history_sha256']:raise RuntimeError('Registered history changed')


if __name__=='__main__':main()

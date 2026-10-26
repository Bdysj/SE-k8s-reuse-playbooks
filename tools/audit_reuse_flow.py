"""Independent final audit of flow order, actions, freshness and reset receipts."""
import argparse
import json
import time
from pathlib import Path

import reuse_flow_executor as ex
from reuse_validation import Adapter


def audit(out):
    plan=json.loads((out/'plan.json').read_text());failures=[];records=[]
    def require(test,label):
        if not test:failures.append(label)
    for name,h in plan['source_hashes'].items():
        require(ex.sha(ex.ROOT/'tools'/name)==h,'active source changed '+name)
        require(ex.sha(out/'tool-snapshot'/name)==h,'archived source changed '+name)
    require(ex.sha(ex.ROOT/'candidates/registration.v1.json')==plan['registration_sha256'],'history changed')
    for key,h in plan['candidate_hashes'].items():require(ex.sha(ex.ROOT/'candidates'/(key+'.yaml'))==h,'candidate changed '+key)
    for case in plan['cases']:
        directory=ex.ROOT/case['carried_from_directory'] if case.get('carried_from_directory') else out/case['name']
        path=directory/'acceptance.json'
        if case.get('carried_from_directory'):
            require(ex.sha(path)==case['carried_acceptance_sha256'],case['name']+' carried receipt identity')
        require(path.exists(),'missing '+case['name'])
        if not path.exists():continue
        r=json.loads(path.read_text());w=r.get('workflow',{})
        require(r['passed'] and r['main_trials']==0,case['name']+' acceptance')
        require(r['primary_post_reset_confirmed'],case['name']+' primary reset')
        if plan.get('environment_profile'):
            require(r.get('primary_environment_after_reset',{}).get('passed'),case['name']+' resolver baseline reset')
            require(r.get('primary_environment_after_reset',{}).get('profile')==plan['environment_profile'],
                case['name']+' fixed resolver environment identity')
            require(r.get('replica_environment_after_reset',{}).get('passed')
                if (directory/'replica/request.json').exists() else True,case['name']+' replica resolver reset')
            setup=json.loads((directory/'primary/incident-setup.json').read_text())
            require(setup.get('environment_check',{}).get('passed'),case['name']+' resolver baseline before fault')
        require(w.get('history_preserved'),case['name']+' immutable history')
        require(w.get('configuration')==case['configuration'],case['name']+' declared actual decision budgets')
        require(w.get('initial_decision')==case['initial_decision'],case['name']+' initial rule')
        require(w.get('execution_permitted') is case['execution_permitted'],case['name']+' permission rule')
        actions=int((directory/'primary/live-repair/record.json').exists() and
            json.loads((directory/'primary/live-repair/record.json').read_text()).get('action_executed',False))
        require(actions==int(case['execution_permitted']),case['name']+' actual candidate action count')
        evidence_events=[e for e in w.get('events',[]) if e['kind']=='isolated_validation' and e.get('evidence')]
        for event in evidence_events:
            c=event['evidence'];request_path=directory/'replica/request.json'
            try:Adapter.verify_certificate(c,json.loads(request_path.read_text()),request_path)
            except Exception as error:require(False,case['name']+' certificate '+str(error))
            require(c['published_at']>=c['completed_at'] and c['outcome_fixed_at']>=c['completed_at'],case['name']+' actual outcome timestamp')
            rr=json.loads((directory/'replica/repair/record.json').read_text())
            require(c['published_at']<rr['completed_at'],case['name']+' outcome published before cleanup')
            if case['execution_permitted']:
                live=json.loads((directory/'primary/live-repair/record.json').read_text())
                require(0<=live['action_started_at']-c['completed_at']<=case['configuration']['observation_ttl'],case['name']+' outcome still fresh at live action')
        if not case['execution_permitted']:
            require(w['fallback_required'] and r['live_outcome']=='Not executed',case['name']+' declined action separation')
        require(all(e.get('kind')!='isolated_validation' for e in w.get('events',[])) if case['name'].startswith(('01-','08-','09-','10-')) else True,
            case['name']+' unnecessary replay')
        if case['use_genuinely_aged_cache']:
            require(w['initial_decision']=='Revalidate' and w['refreshes']==1 and w['replays']==0,case['name']+' stale refresh order')
        if case['expected_timeout']:
            require(r.get('controlled_timeout_reset_accepted'),case['name']+' controlled timeout cleanup')
        records.append({'case':case['name'],'initial_decision':w.get('initial_decision'),
            'actual_record_directory':str(directory.relative_to(ex.ROOT)),
            'final_decision':w.get('final_decision'),'live_actions':actions,'live_outcome':r.get('live_outcome'),
            'replica_outcome':evidence_events[-1]['evidence']['outcome'] if evidence_events else None,
            'acceptance_sha256':ex.sha(path)})
    return {'at':time.time(),'type':'complete_reuse_flow_development_acceptance','accepted':not failures,
        'failures':failures,'records':records,'main_trials':0,'registered_main_scenarios':0,
        'plan_sha256':ex.sha(out/'plan.json'),'scope':plan['scope']}


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    out=ap.parse_args().output.resolve();answer=audit(out);ex.save(out/'final-acceptance.json',answer)
    print(json.dumps(answer,indent=2))
    if not answer['accepted']:raise SystemExit(1)

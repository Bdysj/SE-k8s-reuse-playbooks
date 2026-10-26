"""Read-only acceptance of complete development traces, retaining repair failures."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import main_verifier as v
import registration_trials as reg

ROOT=Path(__file__).resolve().parent.parent


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def trace_checks(record, timing):
    failures=[]
    def require(value,reason):
        if not value:failures.append(reason)
    r=record
    require(r.get('type')=='pre-main_executor_development' and r.get('main_trials')==0
            and r.get('registered') is False,'development/main boundary')
    require(r.get('completed_at') is not None and r.get('executor_evidence_complete') is True,'completed evidence')
    require(r.get('observation_complete') is True and r.get('post_reset_confirmed') is True,'complete observation and reset')
    require(not r.get('collection_or_setup_error') and not r.get('reset_error'),'no hidden harness error')
    require(bool(r.get('setup_checks')) and r['setup_checks'][-1].get('passed') is True,'healthy construction')
    require(bool(r.get('healthy_window')) and all(x['target']['healthy'] and x['non_target']['passed']
                                                for x in r.get('healthy_window',[])),'current-owner healthy gate')
    require(bool(r.get('fault_confirmation')) and r['fault_confirmation'][-1].get('healthy') is False,'observed fault')
    samples=r.get('samples',[])
    require(bool(samples) and samples[-1].get('window_seconds',-1)>=timing['outcome_window'],'full final target window')
    require(r.get('window_seconds',0)>=timing['outcome_window'],'full elapsed window')
    require(r.get('outcome_fixed_at',0)<=r.get('completed_at',0),'outcome before final reset completion')
    if r.get('candidate')=='P1':require(r.get('finished_before_expiry') is True,'PodChaos expiry excluded')
    if r.get('arm')=='no-repair':
        require(r.get('action_executed') is False and r.get('execution_complete') is None,'independent no-repair arm')
        require(r.get('no_repair_persistence_confirmed') is True and all(x['healthy'] is False for x in samples),'persistent fault')
        nt=r.get('pre_action_non_target',[])
        require(bool(nt) and all(x.get('passed') is True for x in nt),'non-target no-repair window')
    elif r.get('arm')=='repair':
        require(r.get('action_executed') is True and not r.get('common_gate_blocked'),'candidate actually admitted')
        require(r.get('action_start_seconds',-1)>=timing['pre_action'],'pre-action period')
        require(all(not x['healthy'] for x in samples if x['window_seconds']<r.get('action_start_seconds',0)),
                'fault did not self-resolve before action')
        pre=r.get('non_target_immediately_before_action')
        require(pre and pre.get('passed') is True,'synchronous pre-action non-target check')
        if pre:
            require(0<=r['action_started_at']-pre['started_at']<=timing['pre_check_max_age'],'pre-check age')
            require(0<=r['action_started_at']-pre['completed_at']<=timing['launch_after_pre_check_max_seconds'],'immediate launch')
        require(r.get('common_safety_gate',{}).get('passed') is True,'common safety gate')
        observed=r.get('pre_action_policy_observation',{})
        require(isinstance(observed.get('started_at'),(int,float))
                and 0<=r['action_started_at']-observed['started_at']<=30,'policy observation age')
        ex=r.get('execution',{})
        events=ex.get('task_events',[])
        successes={x.get('task') for x in events if x.get('event')=='runner_on_ok'}
        necessary=reg.REQUIRED_ACTIONS[r['candidate']]
        execution=(ex.get('rc')==0 and ex.get('status')=='successful' and bool(ex.get('stats'))
            and not any(x.get('event') in ('runner_on_failed','runner_on_unreachable') for x in events)
            and any(x.get('changed') for x in events) and set(necessary)<=successes)
        require(r.get('execution_complete') is execution and ex.get('execution_complete') is execution,
                'necessary-action execution recomputation')
        regression,_=v.regression(pre,r.get('post_action_non_target',[]),r.get('observation_complete'))
        require(r.get('non_target_regression') is regression,'named regression recomputation')
        if r.get('target_recovery') is True:
            first=next((x for x in samples if x['healthy'] and x['window_seconds']>=r['action_start_seconds']),None)
            require(first is not None,'actual recovered sample')
            if first:
                require(first['window_seconds']-r['action_start_seconds']<=timing['repair_budget'],'bounded recovery')
                require(all(x['healthy'] for x in samples if x['window_seconds']>=first['window_seconds']),
                        'recovery remained healthy')
                require(samples[-1]['window_seconds']-first['window_seconds']>=timing['sustained_recovery'],
                        'sustained recovery window')
        elif r.get('target_recovery') is False:
            require(r.get('observation_complete') is True,'failure observed over complete window')
        else:
            require(False,'unobserved recovery flag')
        require(r.get('repair_outcome')==v.outcome(r.get('execution_complete'),r.get('target_recovery'),
                r.get('non_target_regression'),True),'outcome recomputation')
    else:
        require(False,'unknown arm')
    return {'accepted':not failures,'failures':failures}


def audit(path):
    path=Path(path).resolve();out=path.parent.parent
    r=json.loads(path.read_text());protocol=out/'protocol.json';p=json.loads(protocol.read_text())
    result=trace_checks(r,p['timing'])
    if r['protocol_sha256']!=sha(protocol):result['failures'].append('protocol identity')
    if r['candidate_sha256']!=p['candidate_hashes'][r['candidate']]:result['failures'].append('candidate identity')
    if r['candidate']=='P2' and r.get('construction',{}).get('fault_operator')=='decrement_replicas_clamp_zero':
        cfg=r['construction'];fault=r.get('injection',{})
        expected=max(0,cfg['healthy_replicas_before_fault']-2)
        if (cfg.get('fault_decrement')!=2 or cfg.get('fault_replicas')!=expected
                or fault.get('operator')!=cfg['fault_operator'] or fault.get('decrement')!=2
                or fault.get('before_replicas')!=cfg['healthy_replicas_before_fault']
                or fault.get('replicas')!=expected):
            result['failures'].append('same-parameter relative fault proof')
        if not r.get('fault_confirmation') or r['fault_confirmation'][-1].get('workload',{}).get('desired')!=expected:
            result['failures'].append('observed post-fault replica count')
    for name in ('initial-reset.log','post-reset.log'):
        log=path.parent/name
        if not log.exists() or 'baseline confirmed' not in log.read_text():result['failures'].append(name+' proof')
    freeze=json.loads((out/'executor-freeze.json').read_text())
    for name,h in freeze['tool_hashes'].items():
        if sha(out/'tool-snapshot'/name)!=h:result['failures'].append('archived source identity '+name)
    result.update(accepted=not result['failures'],record=str(path.relative_to(ROOT)),record_sha256=sha(path),
        protocol=str(protocol.relative_to(ROOT)),protocol_sha256=sha(protocol),main_trials=0,
        repair_outcome=r.get('repair_outcome'),development_only=True)
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--record',type=Path,action='append',required=True)
    ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    if args.output.exists():raise RuntimeError('Preserve earlier acceptance receipts')
    rows=[audit(p) for p in args.record]
    receipt={'at':time.time(),'stage':'pre-main development','records':rows,
        'accepted':all(r['accepted'] for r in rows),'main_trials':0,
        'auditor_source_sha256':sha(Path(__file__)),
        'scope':'Complete accepted development traces include real repair successes and failures; '
                'construction/no-repair checks are not repair labels or main trials.'}
    args.output.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({'accepted':receipt['accepted'],'records':len(rows),'main_trials':0}))
    if not receipt['accepted']:raise SystemExit(1)


if __name__=='__main__':main()

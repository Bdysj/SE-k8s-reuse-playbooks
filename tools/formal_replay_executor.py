"""Prospective frozen RQ1 replay executor, derived from accepted v4.

Independent outcomes never enter pre-action policies. Calibration, controls and
formal main trials retain distinct identities; any unsafe reset stops collection.
"""
import argparse
import hashlib
import json
import subprocess
import time
import traceback
from pathlib import Path

import main_verifier as v
import reuse_assessor as assessor
import reuse_evidence as evidence
import supported_reuse_v2 as supported
import formal_methods as methods
from run_reuse_flow_development import confirm_environment, preserve_failed_setup_logs
import replica_fault
import qualification as q
import registration_trials as reg
import reset_baseline as rb
import scenario_feasibility as s
from inject_fault import inject

ROOT = rb.ROOT
SOURCES = ['main_executor_v4.py', 'prepare_executor_v4.py', 'replica_fault.py', 'supported_reuse.py', 'reuse_assessor.py', 'reuse_evidence.py', 'main_verifier.py', 'prepare_main_development.py',
           'qualification.py', 'registration_trials.py', 'runner_control.py',
           'scenario_feasibility.py', 'reset_baseline.py', 'inject_fault.py']


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    temp = p.with_suffix(p.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2)+'\n')
    temp.replace(p)


def freeze(out):
    path = out/'executor-freeze.json'
    if path.exists():
        raise RuntimeError('Preserve the prospective executor version')
    snapshot = out/'tool-snapshot'
    snapshot.mkdir()
    for name in SOURCES:
        (snapshot/name).write_bytes((ROOT/'tools'/name).read_bytes())
    save(path, {'at':time.time(), 'stage':'development only', 'protocol_sha256':sha(out/'protocol.json'),
                'tool_hashes':{n:sha(ROOT/'tools'/n) for n in SOURCES},
                'inventory_sha256':sha(ROOT/'tools/ansible-inventory.ini')})


def inputs(out):
    f = json.loads((out/'executor-freeze.json').read_text())
    assert sha(out/'protocol.json') == f['protocol_sha256'], 'Prospective protocol changed'
    for name, h in f['tool_hashes'].items():
        assert sha(ROOT/'tools'/name) == h, 'Prospective source changed: ' + name
    assert sha(ROOT/'tools/ansible-inventory.ini') == f['inventory_sha256']
    p = json.loads((out/'protocol.json').read_text())
    assert not p['main_protocol_ready'] and p['main_trials'] == 0
    assert sha(ROOT/p['manifest']) == p['manifest_sha256']
    for key, h in p['candidate_hashes'].items():
        assert sha(ROOT/'candidates'/(key+'.yaml')) == h, 'Candidate changed'
    for row in p['scenarios'] + p['controls']:
        assert sha(ROOT/row['healthy_manifest']) == row['manifest_sha256']
    return p


def inject_current(row):
    cfg = row['construction']; key = row['candidate']
    if key == 'P2':
        before = rb.kjson('get','deployment',cfg['target_deployment'])
        count = before['spec']['replicas']
        if count != cfg['healthy_replicas_before_fault']:
            raise RuntimeError('P2 healthy replica conditions changed before injection')
        after = replica_fault.desired_after_fault(count,cfg['fault_decrement'])
        if after != cfg['fault_replicas']:
            raise RuntimeError('Prospective P2 fault arithmetic disagrees with the declared setup')
        rb.kubectl('scale','deployment',cfg['target_deployment'],'--replicas='+str(after))
        return {'kind':'underprovision','operator':cfg['fault_operator'],'decrement':cfg['fault_decrement'],
                'deployment':cfg['target_deployment'],'deployment_uid':before['metadata']['uid'],
                'before_replicas':count,'replicas':after}
    if key in ('P1', 'P3'):
        resources = None if key == 'P1' else {
            'requests':{'cpu':'1m', 'memory':cfg['fault_memory']},
            'limits':{'cpu':'2m', 'memory':cfg['fault_memory']}}
        return inject('pod-failure' if key == 'P1' else 'pod-config-error',
                      cfg['target_deployment'], cfg['target_container'],
                      namespace=rb.NS, context=rb.CONTEXT, resource_values=resources)
    return s.inject_row(row)


def apply_variant(row, manifest):
    replacement = s.apply_changes(row, manifest)
    if row['construction'].get('replace_pod') == 'mysql':
        end = time.monotonic()+90
        while True:
            try:
                cities = rb.run_db_client('SELECT COUNT(*) FROM cities.cities', 'shipping')
                ratings = rb.run_db_client('SELECT COUNT(*) FROM ratings.ratings', 'ratings')
                if cities == '948833' and ratings.isdigit():
                    break
            except (RuntimeError, rb.Failure, subprocess.SubprocessError):
                pass
            if time.monotonic() >= end:
                raise RuntimeError('Replaced MySQL did not pass real Service/account SQL readiness')
            time.sleep(3)
    return replacement


def run_case(row, arm, path, p):
    path.mkdir(parents=True, exist_ok=False)
    key = row['candidate']; timing = p['timing']; file = path/'record.json'
    r = {'type':p['record_type'], 'scenario':row['id'], 'candidate':key,
         'arm':arm, 'main_trial':p['record_type']=='formal_main_replay', 'registered':p['record_type']=='formal_main_replay', 'trial_identity':p['trial_identity'], 'started_at':time.time(),
         'construction':row['construction'], 'candidate_sha256':p['candidate_hashes'][key],
         'protocol_sha256':p['protocol_sha256'], 'samples':[], 'post_action_non_target':[],
         'action_executed':False, 'execution_complete':None, 'target_recovery':None,
         'non_target_regression':None, 'observation_complete':False}
    thread = runner = None
    safe_reset = True
    try:
        reg.reset_s4(path/'initial-reset.log', p)
        r['environment_after_initial_reset']=confirm_environment(p)
        if not r['environment_after_initial_reset']['passed']:raise RuntimeError('Initial resolver profile differs')
        rb.ensure_probe()
        r['replacement'] = apply_variant(row, ROOT/row['healthy_manifest'])
        r['environment_before_fault']=confirm_environment(p)
        if not r['environment_before_fault']['passed']:raise RuntimeError('Variant resolver profile differs')
        setup_end = time.monotonic()+90
        while True:
            health = s.healthy(row, ROOT/row['healthy_manifest'])
            r.setdefault('setup_checks', []).append(health)
            if health['passed']:
                break
            if time.monotonic() >= setup_end:
                raise RuntimeError('Changed healthy setup did not pass')
            time.sleep(5)
        start = time.monotonic()
        while True:
            health = v.target(row); nt = v.non_target(row)
            r.setdefault('healthy_window', []).append({'target':health, 'non_target':nt})
            if not health['healthy'] or not nt['passed']:
                raise RuntimeError('Current-owner healthy acceptance failed')
            if time.monotonic()-start >= timing['healthy_window']:
                break
            time.sleep(5)
        r['healthy_snapshot'] = methods.namespace_snapshot()
        r['fault_activation_at'] = time.time(); activation = time.monotonic()
        r['injection'] = inject_current(row)
        deadline = time.monotonic()+timing['fault_confirmation']
        while True:
            t = v.target(row)
            r.setdefault('fault_confirmation', []).append(t)
            if not t['healthy']:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Intended fault symptom was not observable')
            time.sleep(3)
        start = time.monotonic(); r['window_started_at'] = time.time()
        last_nt = -timing['non_target_interval']
        recovery_start = None; recovery_lost = False
        while True:
            elapsed = time.monotonic()-start
            if arm == 'repair' and runner is None and elapsed >= timing['pre_action']:
                # Save policy inputs before any replay outcome exists.
                current = assessor.contract(row, p['protocol_path'], p['protocol_sha256'])
                observation = supported.collect(key, current)
                r['pre_action_snapshot'] = methods.scope_snapshot(observation)
                r['initial_method_decisions'] = methods.decisions(p, row, observation, current, r['samples'][0])
                r['non_target_immediately_before_action'] = v.non_target(row)
                pre = r['non_target_immediately_before_action']
                if not pre['passed']:
                    r['pre_action_instability'] = True
                    raise RuntimeError('Pre-action instability: no candidate launched')
                if time.time()-pre['started_at'] > timing['pre_check_max_age']:
                    raise RuntimeError('Pre-action evidence exceeded its fixed age budget')
                allowed = sorted(d['metadata']['name'] for d in observation['deployments']
                                 if d['metadata']['name'] in row['non_target_workloads'] +
                                    [row['construction']['target_deployment']])
                r['pre_action_policy_observation'] = observation
                states = supported.compare(key, p['candidate_hashes'][key], observation, current)
                r['reuse_assessment_observation_only'] = {
                    'prototype':'supported-rq2-frozen-v1', 'candidate':key, 'states':states,
                    'contract_sha256':assessor.digest(current), 'relevant_revision':observation['relevant_revision'],
                    **assessor.decision(states)}
                r['common_safety_gate'] = evidence.common_safety_gate(
                    key, ROOT/'candidates'/(key+'.yaml'), p['candidate_hashes'][key],
                    rb.CONTEXT, rb.NS, observation, allowed)
                r['policy_cannot_skip_rq1_replay'] = True
                save(file, r)
                if not r['common_safety_gate']['passed']:
                    r['common_gate_blocked'] = True
                    raise RuntimeError('Common safety gate blocked the candidate: '+str(r['common_safety_gate']['failures']))
                if time.time()-observation['started_at'] > 30:
                    raise RuntimeError('Pre-action policy observation became stale before launch')
                if time.time()-pre['completed_at'] > timing['launch_after_pre_check_max_seconds']:
                    raise RuntimeError('Synchronous pre-action gate became stale before launch')
                r['pre_action_relevant_revision']=observation['relevant_revision']
                r['action_started_at'] = time.time(); r['action_start_seconds'] = time.monotonic()-start
                thread, runner = reg.start_runner(key, path)
                r['action_executed'] = True
            if runner is not None and thread.is_alive() and time.monotonic()-start-r['action_start_seconds'] > timing['repair_budget']:
                runner.cancel()
                if thread.is_alive():
                    safe_reset = False
                    raise RuntimeError('Runner termination unconfirmed; refuse concurrent reset')
            if runner is not None and not thread.is_alive() and 'execution' not in r:
                r['execution'] = reg.execution_s4(runner)
                r['execution_complete'] = r['execution']['execution_complete']
                r['execution_finished_at'] = time.time()
            t = v.target(row); t['window_seconds'] = time.monotonic()-start
            r['samples'].append(t)
            if not r['action_executed'] and t['healthy']:
                raise RuntimeError('Fault self-resolved before candidate execution')
            if 'execution' in r:
                if t['healthy'] and recovery_start is None:
                    recovery_start = t['window_seconds']
                elif not t['healthy'] and recovery_start is not None:
                    recovery_lost = True
            if time.monotonic()-start-last_nt >= timing['non_target_interval'] or t['window_seconds'] >= timing['outcome_window']:
                nt = v.non_target(row)
                nt['window_seconds'] = time.monotonic()-start
                key_nt = 'post_action_non_target' if r['action_executed'] else 'pre_action_non_target'
                r.setdefault(key_nt, []).append(nt)
                last_nt = time.monotonic()-start
            save(file, r)
            if t['window_seconds'] >= timing['outcome_window']:
                break
            time.sleep(min(timing['sample_interval'], max(0, timing['outcome_window']-(time.monotonic()-start))))
        r['window_seconds'] = time.monotonic()-start
        r['observation_complete'] = True
        if key == 'P1':
            r['fault_active_at_end'] = rb.kjson('get', 'podchaos', 'pilot-pod-fail')
            r['finished_before_expiry'] = time.monotonic()-activation < 1200
            if not r['finished_before_expiry']:
                r['observation_complete'] = False
                raise RuntimeError('Fault expiry overlapped the evaluation')
        if arm == 'repair':
            r['sustained_recovery_seconds'] = None if recovery_start is None else r['samples'][-1]['window_seconds']-recovery_start
            r['target_recovery'] = (recovery_start is not None and not recovery_lost
                and recovery_start-r['action_start_seconds'] <= timing['repair_budget']
                and r['sustained_recovery_seconds'] >= timing['sustained_recovery'])
        else:
            r['no_repair_persistence_confirmed'] = all(not x['healthy'] for x in r['samples'])
    except Exception as error:
        r.update(collection_or_setup_error=str(error), traceback=traceback.format_exc())
        if not r['action_executed']:preserve_failed_setup_logs(path)
    finally:
        if runner is not None:
            try:
                if thread.is_alive():
                    runner.cancel()
                if thread.is_alive():
                    safe_reset = False
                else:
                    r['execution'] = reg.execution_s4(runner)
                    r['execution_complete'] = r['execution']['execution_complete']
            except Exception as error:
                safe_reset = False; r['termination_error'] = str(error)
        r['non_target_regression'], r['regressed_checks'] = v.regression(
            r.get('non_target_immediately_before_action'), r['post_action_non_target'], r['observation_complete'])
        r['repair_outcome'] = v.outcome(r['execution_complete'], r['target_recovery'],
                                      r['non_target_regression'], r['action_executed'])
        r['outcome_fixed_at'] = time.time()
        save(file, r)
        if safe_reset:
            try:
                rb.kubectl('delete', 'podchaos', 'pilot-pod-fail', '--ignore-not-found', '--timeout=60s')
                for name in row['construction'].get('delete_deployments_before', []):
                    rb.kubectl('delete', 'deployment', name, '--ignore-not-found', '--wait=true', '--timeout=120s')
                reg.reset_s4(path/'post-reset.log', p)
                r['environment_after_final_reset']=confirm_environment(p)
                if not r['environment_after_final_reset']['passed']:raise RuntimeError('Final resolver profile differs')
                r['post_reset_confirmed'] = True
            except Exception as error:
                r.update(post_reset_confirmed=False, reset_error=str(error))
        else:
            r.update(post_reset_confirmed=False, reset_error='Runner termination not confirmed')
        r['completed_at'] = time.time()
        r['executor_evidence_complete'] = r['observation_complete'] and r.get('post_reset_confirmed', False) and not r.get('collection_or_setup_error')
        save(file, r)
    return r


"""Prospective isolated replay adapter. Actual outcome time precedes cleanup.

Worker subprocesses keep replica globals separate from incident globals. Only
an immutable outcome record that passes independent trace and fidelity checks
can resolve explicitly mapped Unknown requirements. Reset remains separately
audited and blocks the next use of that replica, even after evidence is released.
"""
import argparse
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import audit_main_development as audit
import main_verifier as v
import reset_baseline as rb
import reuse_assessor as a
import reuse_evidence as evidence
import reuse_flow_executor as ex
import supported_reuse_v2 as supported
from prepare_replica_development import translate
from run_replica_development import k, node_facts, volumes, docker_roots

PRIMARY = 'k3d-lab'
REPLICA = 'k3d-lab-validation'
MAPPINGS = {
    'target_objects': 'current logical owner, registered discovery, and same frozen action',
    'referenced_fields': 'same template/placement/consumption and actual necessary actions plus current rollout',
    'resource_relationships': 'same Service selectors/ports/configuration and owner-bound current checks',
    'verification_criteria': 'identical attributed current criterion, full recovery and named non-target window',
}


def write_once(path, value):
    temporary=path.with_suffix(path.suffix+'.publication-tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n')
    os.link(temporary,path)  # exclusive publication; readers never see half JSON
    temporary.unlink()


def in_context(context, operation):
    before = rb.CONTEXT
    try:
        rb.CONTEXT = context
        return operation()
    finally:
        rb.CONTEXT = before


def normalise_service_ports(spec, declared):
    """API-assigned external ports are identities only in this internal route scope.

    Explicit nodePort bindings stay exact. Service port/targetPort/protocol/name
    are never removed. Raw assigned ports are archived in the fidelity receipt.
    """
    values={p.get('name'):p for p in declared.get('ports',[])}
    for port in spec.get('ports',[]):
        source=values.get(port.get('name'))
        if source is not None and 'nodePort' not in source:
            port.pop('nodePort',None)
    return spec


def strict_application(context, manifest):
    """Full application specs; only cluster-assigned Service addresses excluded.

    UIDs, status and managed metadata are identities, not application specs.
    Unlike the baseline fingerprint, full rollout strategy is retained here.
    """
    import yaml
    declared={d['metadata']['name']:d['spec'] for d in yaml.safe_load_all(manifest.read_text())
              if d and d.get('kind')=='Service'}
    result = {}
    for kind in ('deployments', 'statefulsets', 'services', 'configmaps'):
        objects = json.loads(k(context, 'get', kind, '-o', 'json'))['items']
        values = {}
        for obj in objects:
            name = obj['metadata']['name']
            if kind == 'configmaps' and name == 'kube-root-ca.crt':
                continue  # cluster CA identity, never application configuration
            spec = copy.deepcopy(obj.get('spec', {}))
            if kind == 'services':
                spec.pop('clusterIP', None)
                spec.pop('clusterIPs', None)
                spec=normalise_service_ports(spec,declared.get(name,{}))
            values[name] = {'spec': spec, 'data': obj.get('data'),
                            'binaryData': obj.get('binaryData'), 'immutable': obj.get('immutable')}
        result[kind] = values
    return result


def outcome_checks(record, timing):
    """Audit phase evidence without inventing a future successful reset."""
    problems = []
    if record.get('collection_or_setup_error'):
        problems.append('collection/setup error')
    if not record.get('observation_complete'):
        problems.append('incomplete outcome window')
    if record.get('arm') != 'repair' or not record.get('action_executed'):
        problems.append('no actual repair action')
    if not record.get('setup_checks') or not record['setup_checks'][-1]['passed']:
        problems.append('healthy construction')
    if not record.get('healthy_window') or not all(
            x['target']['healthy'] and x['non_target']['passed'] for x in record['healthy_window']):
        problems.append('healthy current-owner window')
    if not record.get('fault_confirmation') or record['fault_confirmation'][-1]['healthy']:
        problems.append('fault confirmation')
    samples = record.get('samples', [])
    if not samples or samples[-1].get('window_seconds', 0) < timing['outcome_window']:
        problems.append('target window incomplete')
    pre = record.get('non_target_immediately_before_action')
    started = record.get('action_started_at', 0)
    if not pre or not pre['passed'] or not 0 <= started-pre['started_at'] <= timing['pre_check_max_age']:
        problems.append('pre-action named checks')
    if pre and not 0 <= started-pre['completed_at'] <= timing['launch_after_pre_check_max_seconds']:
        problems.append('launch after pre-action check')
    if not record.get('common_safety_gate', {}).get('passed'):
        problems.append('common gate')
    observed = record.get('pre_action_policy_observation', {})
    if not 0 <= started-observed.get('started_at', 0) <= 30:
        problems.append('policy observation freshness')
    if record.get('action_start_seconds', -1) < timing['pre_action']:
        problems.append('pre-action observation period')
    execution = record.get('execution', {})
    events = execution.get('task_events', [])
    necessary = audit.reg.REQUIRED_ACTIONS[record['candidate']]
    reached = {x.get('task') for x in events if x.get('event') == 'runner_on_ok'}
    complete = (execution.get('rc') == 0 and execution.get('status') == 'successful'
        and bool(execution.get('stats')) and any(x.get('changed') for x in events)
        and not any(x.get('event') in ('runner_on_failed', 'runner_on_unreachable') for x in events)
        and set(necessary) <= reached)
    if record.get('execution_complete') is not bool(complete):
        problems.append('necessary-action execution flag')
    regressed, _ = v.regression(pre, record.get('post_action_non_target', []), record.get('observation_complete'))
    if record.get('non_target_regression') is not regressed:
        problems.append('regression recomputation')
    first = next((s for s in samples if s['healthy'] and s['window_seconds'] >= record['action_start_seconds']), None)
    recovered = (first is not None and first['window_seconds']-record['action_start_seconds'] <= timing['repair_budget']
        and all(s['healthy'] for s in samples if s['window_seconds'] >= first['window_seconds'])
        and samples[-1]['window_seconds']-first['window_seconds'] >= timing['sustained_recovery'])
    if record.get('target_recovery') is not recovered:
        problems.append('recovery recomputation')
    if record.get('repair_outcome') != v.outcome(record.get('execution_complete'),
            record.get('target_recovery'), record.get('non_target_regression'), True):
        problems.append('outcome recomputation')
    if record['candidate'] == 'P1' and record.get('finished_before_expiry') is not True:
        problems.append('fault expiry')
    return {'accepted': not problems, 'failures': problems}


def fidelity(request, replica_record):
    current = request['contract']
    observed = in_context(PRIMARY, lambda: supported.collect(request['candidate'], current))
    primary = strict_application(PRIMARY,ex.ROOT/request['row']['healthy_manifest'])
    replica = strict_application(REPLICA,ex.ROOT/request['replica_row']['healthy_manifest'])
    allocated_ports={context:{s['metadata']['name']:[{'name':p.get('name'),'port':p['port'],
        'targetPort':p.get('targetPort'),'nodePort':p.get('nodePort')} for p in s['spec']['ports']]
        for s in json.loads(k(context,'get','services','-o','json'))['items']} for context in (PRIMARY,REPLICA)}
    np, nr = node_facts(PRIMARY), node_facts(REPLICA)
    vp, vr, roots = volumes(PRIMARY), volumes(REPLICA), docker_roots()
    separate = (set(vp) == set(vr) == {'mysql-data', 'data-redis-0'}
        and all(vp[n]['claim_uid'] != vr[n]['claim_uid'] and vp[n]['pv_uid'] != vr[n]['pv_uid']
                and vp[n]['phase'] == vr[n]['phase'] == 'Bound'
                and all(vp[n][key] == vr[n][key] for key in ('storage','access_modes','storage_class')) for n in vp)
        and len({x['storage_volume'] for x in roots.values()}) == 6)
    target = in_context(PRIMARY, lambda: v.target(request['row']))
    replica_target = v.target(request['replica_row'])
    first = request['injection']; second = replica_record['injection']
    same_fault = (first['kind'] == second['kind'])
    for key in ('operator','decrement','before_replicas','replicas','workload','container',
                'resource_values','duration_seconds','variable','value','service'):
        same_fault = same_fault and first.get(key) == second.get(key)
    # P1 victim identities must differ, with the same declared logical owner.
    if request['candidate'] == 'P1':
        same_fault = same_fault and first['victim_uid'] != second['victim_uid']
        for context in (PRIMARY, REPLICA):
            chaos = json.loads(k(context, 'get', 'podchaos', 'pilot-pod-fail', '-o', 'json'))
            same_fault = same_fault and chaos['spec']['duration'] == '1200s' and chaos['spec']['action'] == 'pod-failure'
    proof = {'at': time.time(), 'primary_spec': primary, 'replica_spec': replica,
        'full_specs_equal_after_exact_role_mapping': translate(primary) == replica,
        'primary_nodes': np, 'replica_nodes': nr, 'node_facts_equal': translate(np) == nr,
        'primary_volumes': vp, 'replica_volumes': vr, 'storage_roots': roots,
        'writable_storage_separate': separate, 'same_declared_and_attained_fault': same_fault,
        'primary_fault_observed': not target['healthy'], 'replica_fault_observed': not replica_target['healthy'],
        'incident_revision_before_replica_action': observed['relevant_revision'],
        'incident_revision_matches_request': observed['relevant_revision'] == request['relevant_revision'],
        'raw_service_port_inventory':allocated_ports,
        'internal_service_route_confirmed':observed.get('client_frontend_service')==current['frontend_service'],
        'normalisation': 'Exact node-role names and cluster-assigned Service IPs/external nodePorts absent from declared manifests. All requests use internal Service DNS; explicit nodePorts and all port/targetPort values stay exact; full strategies retained.'}
    proof['passed'] = all(proof[k] for k in ('full_specs_equal_after_exact_role_mapping',
        'node_facts_equal','writable_storage_separate','same_declared_and_attained_fault',
        'primary_fault_observed','replica_fault_observed','incident_revision_matches_request','internal_service_route_confirmed'))
    return proof


def worker(request_path):
    request = json.loads(request_path.read_text());out = ex.ROOT/request['replica_protocol_directory']
    p = ex.inputs(out); row = request['replica_row']; key = row['candidate']
    primary=ex.inputs(ex.ROOT/request['primary_protocol_directory'])
    expected=primary['scenarios'][0]
    expected_contract=a.contract(expected,str((ex.ROOT/request['primary_protocol_directory']/'protocol.json').relative_to(ex.ROOT)),
                                 ex.sha(ex.ROOT/request['primary_protocol_directory']/'protocol.json'))
    if (request['row']!=expected or request['contract']!=expected_contract
            or row!=p['scenarios'][0] or row['construction']!=translate(expected['construction'])
            or row['candidate']!=expected['candidate'] or request['candidate_sha256']!=p['candidate_hashes'][key]):
        raise RuntimeError('Current criterion/candidate binding differs from the prospective protocol')
    rb.CONTEXT = REPLICA;rb.NS = p['namespace'];rb.MANIFEST = ex.ROOT/p['manifest']
    rb.FINGERPRINT = rb.MANIFEST.with_suffix('.fingerprint.json')
    path = out/'repair';proof = None
    def before_launch(record, observed, contract):
        nonlocal proof
        proof = fidelity(request, record)
        ex.save(out/'fidelity.json', proof)
        return {'passed': proof['passed'], 'fidelity_sha256': ex.sha(out/'fidelity.json')}
    def publish(record):
        check = outcome_checks(record, p['timing'])
        if not check['accepted'] or not proof or not proof['passed']:
            ex.save(out/'outcome-unavailable.json', {'at':time.time(),'trace_audit':check,'fidelity_passed':bool(proof and proof['passed'])})
            return
        phase = out/'outcome-record.json'
        write_once(phase,record)
        stamp = min(record['samples'][-1]['at'], record['post_action_non_target'][-1]['started_at'])
        certificate = {'type':'immutable_actual_isolated_outcome_v1', 'candidate_sha256':request['candidate_sha256'],
            'contract_sha256':a.digest(request['contract']), 'relevant_revision':request['relevant_revision'],
            'request_sha256':ex.sha(request_path),'protocol_sha256':ex.sha(out/'protocol.json'),
            'fidelity_passed': True, 'fidelity_record':str((out/'fidelity.json').relative_to(ex.ROOT)),
            'fidelity_record_sha256':ex.sha(out/'fidelity.json'), 'outcome':record['repair_outcome'],
            'completed_at':stamp,'published_at':time.time(), 'outcome_fixed_at':record['outcome_fixed_at'],
            'verified_requirements':request['unresolved'],
            'verification_mapping':{c:MAPPINGS[c] for c in request['unresolved']},
            'record':str(phase.relative_to(ex.ROOT)), 'record_sha256':ex.sha(phase),
            'outcome_audit':check,'cleanup_status_at_publication':'pending, separate reset acceptance required',
            'main_trials':0}
        write_once(out/'certificate.json',certificate)
        print('Actual immutable outcome published before replica cleanup',flush=True)
    record = ex.run_case(row,'repair',path,p,before_launch=before_launch,on_outcome=publish,
        cancelled=lambda:(out/'cancel-requested.json').exists())
    receipt = audit.audit(path/'record.json')
    ex.save(out/'cleanup-acceptance.json',receipt)
    print(json.dumps({'replica_outcome':record['repair_outcome'],'trace_accepted':receipt['accepted'],
        'reset':record.get('post_reset_confirmed')}),flush=True)
    if not receipt['accepted']:raise SystemExit(1)


class Adapter:
    def __init__(self, case_directory, primary_protocol, row, injection):
        self.out = case_directory; self.p = primary_protocol; self.row = row;self.injection = injection
        self.child = None;self.stream = None;self.request = None

    def validate(self, candidate, candidate_hash, observed, current, unresolved, budget):
        replica = self.out/'replica'
        primary=self.out/'primary'
        expected=a.contract(self.row,str((primary/'protocol.json').relative_to(ex.ROOT)),ex.sha(primary/'protocol.json'))
        if current!=expected or candidate!=self.row['candidate'] or candidate_hash!=self.p['candidate_hashes'][candidate]:
            raise RuntimeError('Validation does not match the exact current contract/candidate')
        if self.child is not None or not unresolved or not set(unresolved) <= set(MAPPINGS):
            raise RuntimeError('Unsupported uncertainty or replica already leased')
        request = {'at':time.time(),'candidate':candidate,'candidate_sha256':candidate_hash,
            'contract':current,'relevant_revision':observed['relevant_revision'],'unresolved':unresolved,
            'row':self.row, 'replica_row':json.loads((replica/'protocol.json').read_text())['scenarios'][0],
            'primary_protocol_directory':str(primary.relative_to(ex.ROOT)),
            'injection':self.injection,'replica_protocol_directory':str(replica.relative_to(ex.ROOT)),
            'remaining_decision_budget_seconds':budget}
        self.request = request;path=replica/'request.json'
        with path.open('x') as f:json.dump(request,f,indent=2)
        self.stream=(replica/'worker.log').open('w')
        self.child=subprocess.Popen([sys.executable,'-u',str(Path(__file__).resolve()),'--worker',str(path)],
            stdout=self.stream,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+budget
        last_guard=-float('inf');guards=[]
        try:
            while time.monotonic()<deadline:
                if time.monotonic()-last_guard>=10:
                    before=supported.collect(candidate,current)
                    target=v.target(self.row);nt=v.non_target(self.row)
                    sample={'at':time.time(),'same_revision':before['relevant_revision']==observed['relevant_revision'],
                        'fault_persists':not target['healthy'],'non_targets_healthy':nt['passed']}
                    guards.append(sample);ex.save(replica/'incident-guard.json',guards)
                    last_guard=time.monotonic()
                    if not all(sample[key] for key in ('same_revision','fault_persists','non_targets_healthy')):
                        ex.save(replica/'cancel-requested.json',{'at':time.time(),'reason':'incident drift or pre-existing instability'})
                        return None
                certificate_path=replica/'certificate.json'
                if certificate_path.exists():
                    c=json.loads(certificate_path.read_text());self.verify_certificate(c,request,path)
                    return c
                if self.child.poll() is not None or (replica/'outcome-unavailable.json').exists():
                    return None
                time.sleep(.5)
            ex.save(replica/'cancel-requested.json',{'at':time.time(),'reason':'remaining decision budget exhausted'})
            return None
        except BaseException:
            ex.save(replica/'cancel-requested.json',{'at':time.time(),'reason':'validation adapter interrupted'})
            raise

    @staticmethod
    def verify_certificate(c, request, path):
        out = path.parent; p=json.loads((out/'protocol.json').read_text())
        record_path=ex.ROOT/c['record']; proof_path=ex.ROOT/c['fidelity_record']
        if record_path.parent != out or proof_path.parent != out:raise RuntimeError('Certificate outside leased replica')
        record=json.loads(record_path.read_text());proof=json.loads(proof_path.read_text())
        if (c.get('type')!='immutable_actual_isolated_outcome_v1' or c['request_sha256']!=ex.sha(path)
            or c['candidate_sha256']!=request['candidate_sha256'] or c['contract_sha256']!=a.digest(request['contract'])
            or c['relevant_revision']!=request['relevant_revision'] or c['protocol_sha256']!=ex.sha(out/'protocol.json')
            or c['record_sha256']!=ex.sha(record_path) or c['fidelity_record_sha256']!=ex.sha(proof_path)
            or c.get('fidelity_passed') is not True or proof.get('passed') is not True
            or c['verified_requirements']!=request['unresolved'] or c['outcome']!=record['repair_outcome']
            or record['candidate_sha256']!=c['candidate_sha256'] or record['protocol_sha256']!=c['protocol_sha256']
            or record['construction']!=request['replica_row']['construction']
            or c['completed_at']!=min(record['samples'][-1]['at'],record['post_action_non_target'][-1]['started_at'])
            or not outcome_checks(record,p['timing'])['accepted']):
            raise RuntimeError('Immutable validation certificate/provenance did not verify')
        return True

    def finish(self):
        if self.child is None:return {'replica_used':False,'passed':True}
        self.child.wait(timeout=1800);self.stream.close()
        receipt=json.loads((self.out/'replica/cleanup-acceptance.json').read_text())
        return {'replica_used':True,'passed':self.child.returncode==0 and receipt['accepted'],
            'receipt':receipt,'returncode':self.child.returncode}


if __name__ == '__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--worker',type=Path,required=True)
    worker(ap.parse_args().worker.resolve())

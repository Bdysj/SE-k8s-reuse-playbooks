"""Serial registered method-controlled responses; independent audit before advancement."""
import argparse
import fcntl
import json
import shutil
import subprocess
import time
from pathlib import Path

import audit_rq3a_response_pilots_v3 as audit
import reuse_flow_executor as ex
from rq3a_registered_incident_v3 import run_one

PREPARATION = ex.ROOT/'results/RQ3a-response-preparation-v2'
NODES = ['k3d-lab-server-0', 'k3d-lab-agent-0', 'k3d-lab-agent-1', 'k3d-lab-serverlb',
         'k3d-lab-validation-server-0', 'k3d-lab-validation-agent-0',
         'k3d-lab-validation-agent-1', 'k3d-lab-validation-serverlb']


def check(out):
    protocol = json.loads((out/'protocol.json').read_text())
    frozen = json.loads((out/'freeze.json').read_text())
    assert ex.sha(out/'protocol.json') == frozen['protocol_sha256']
    assert protocol['response_registered'] and len(protocol['queue']) == protocol['response_trials'] == 240
    for name, digest in frozen['source_hashes'].items():
        assert ex.sha(ex.ROOT/'tools'/name) == digest, name
        assert ex.sha(out/'tool-snapshot'/name) == digest, name
    for name, digest in protocol['pilot_evidence'].items():
        assert ex.sha(ex.ROOT/protocol['pilot_directory']/name) == digest, name
    for data in protocol['inputs'].values():
        assert ex.sha(ex.ROOT/data['path']) == data['sha256']
    assert ex.sha(ex.ROOT/protocol['source_lock']['path']) == protocol['source_lock']['sha256']
    assert ex.sha(ex.ROOT/'candidates/registration.v1.json') == protocol['registration_sha256']
    for key, digest in protocol['candidate_hashes'].items():
        assert ex.sha(ex.ROOT/'candidates'/(key+'.yaml')) == digest
    amendment = json.loads((out/'amendment.json').read_text())
    assert ex.sha(out/'amendment.json') == frozen['amendment_sha256']
    assert protocol['incident_executor'] == 'rq3a_registered_incident_v3.py'
    assert ex.sha(ex.ROOT/amendment['previous_protocol']) == amendment['previous_protocol_sha256']
    assert ex.sha(ex.ROOT/amendment['preserved_rejected_attempts']) == amendment['preserved_rejected_attempts_sha256']
    for field in ('recovery_acceptance', 'unit_acceptance'):
        assert ex.sha(ex.ROOT/amendment[field]) == amendment[field+'_sha256']
    for trial in protocol['queue'][:protocol['carried_responses']]:
        assert (out/trial['name']).resolve() == (ex.ROOT/trial['carried_from_directory']).resolve()
        assert ex.sha(out/trial['name']/'acceptance.json') == trial['carried_acceptance_sha256']
        if trial.get('acceptance_override'):
            assert ex.sha(ex.ROOT/trial['acceptance_override']) == trial['acceptance_override_sha256']
    return protocol


def publish(out, value):
    retained = json.loads((out/'rejected-attempts.json').read_text())
    value = {**value, 'retained_rejected_attempts': len(retained),
             'retained_executed_indeterminate_attempts': sum(r['candidate_action_executed']
                 and r['live_outcome'] == 'Indeterminate' for r in retained), 'at': time.time(), 'responses_planned': 240, 'formal_registered': True,
             'formal_progress': str((out/'progress.json').relative_to(ex.ROOT))}
    ex.save(out/'progress.json', value)
    ex.save(PREPARATION/'progress.json', value)


def totals(records):
    by_method = {}
    for method in ('proposed', 'fault_matching', 'whole_environment'):
        selected = [r for r in records if r['method'] == method]
        by_method[method] = {'accepted_responses': len(selected),
            'live_executions': sum(r['actual_live_execution'] for r in selected),
            'launch_refusals': sum(r.get('launch_permission_blocked') is True for r in selected),
            'successful_repairs': sum(r['live_outcome'] == 'Successful repair' for r in selected),
            'confirmed_failures': sum(r['live_outcome'] == 'Confirmed failure' for r in selected),
            'not_executed': sum(r['live_outcome'] == 'Not executed' for r in selected),
            'revalidation_requests': sum(r['revalidation_requests'] for r in selected),
            'actual_isolated_executions': sum(r['actual_isolated_execution'] for r in selected),
            'isolated_confirmed_failures': sum(r['isolated_execution_outcome'] == 'Confirmed failure' for r in selected),
            'isolated_indeterminate_executions': sum(r['actual_isolated_execution'] and
                r['isolated_execution_outcome'] == 'Indeterminate' for r in selected)}
    return by_method


def run(out):
    p = check(out)
    records = []
    for trial in p['queue']:
        receipt = (out/'audit-overrides'/trial['name']/'independent-audit.json'
                   if trial.get('acceptance_override') else out/trial['name']/'independent-audit.json')
        if receipt.exists():
            record = json.loads(receipt.read_text())
            assert record['accepted']
            rerun = audit.audit_case(out/trial['name'], acceptance_override=trial.get('acceptance_override'))
            assert rerun['accepted'] and rerun['acceptance_sha256'] == record['acceptance_sha256']
            records.append(record)
        else:
            break
    with (ex.ROOT/'results/rq3a-response-incident.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for trial in p['queue'][len(records):]:
            check(out)
            if shutil.disk_usage(out).free < p['disk_guard_minimum_free_bytes']:
                publish(out, {'state': 'halted', 'position': trial['position'],
                    'responses_completed': len(records), 'reason': 'Disk guard below2GiB; no new fault/action started'})
                raise SystemExit(1)
            directory = out/trial['name']
            if (directory/'acceptance.json').exists():
                raise RuntimeError('Preserve rejected attempt; a separately archived prospective retry is required')
            for role, digest in trial['protocol_sha256'].items():
                assert ex.sha(directory/role/'protocol.json') == digest
            publish(out, {'state': 'running', 'position': trial['position'],
                'responses_completed': len(records), 'current_trial': trial['trial_id'],
                'current_method': trial['method'], 'per_method': totals(records)})
            result = run_one(out, trial)
            try:
                checked = audit.audit_case(directory, acceptance_override=trial.get('acceptance_override')) if result['passed'] else {
                    'at': time.time(), 'accepted': False, 'failures': [result.get('error', 'incident boundary rejected')]}
            except Exception as error:
                checked = {'at': time.time(), 'accepted': False,
                           'failures': ['Independent audit unavailable: '+str(error)]}
            ex.save(directory/'independent-audit.json', checked)
            if not checked['accepted']:
                raw = directory/'primary/live-repair/record.json'
                executed = json.loads(raw.read_text()) if raw.exists() else {}
                rejection = {'at': time.time(), 'trial_identity': trial, 'record_directory': str(directory.relative_to(ex.ROOT)),
                    'candidate_action_executed': executed.get('action_executed') is True,
                    'live_outcome': executed.get('repair_outcome', 'Not executed'),
                    'primary_reset_confirmed': result.get('primary_post_reset_confirmed'),
                    'audit': checked, 'retention': 'Keep this attempt even if a later explicit retry fills the planned slot.'}
                retained = json.loads((out/'rejected-attempts.json').read_text())
                ex.save(out/'rejected-attempts.json', retained + [rejection])
                publish(out, {'state': 'halted', 'position': trial['position'], 'responses_completed': len(records),
                    'reason': checked['failures'], 'retained_rejected_attempts': 1,
                    'retained_executed_indeterminate_attempts': int(rejection['candidate_action_executed']
                                                                   and rejection['live_outcome'] == 'Indeterminate')})
                raise SystemExit(1)
            assert result['formal_registered'] and result['response_trials'] == 1
            assert result['trial_identity'] == {k: trial[k] for k in
                ('position', 'scenario', 'candidate', 'repeat', 'method', 'matched_block', 'trial_id')}
            records.append(checked)
            ex.save(out/'summary.json', {'at': time.time(), 'accepted_responses': len(records),
                'per_method': totals(records), 'records': records, 'rq3b_started': False})
            publish(out, {'state': 'running', 'position': trial['position'],
                'responses_completed': len(records), 'current_trial': trial['trial_id'],
                'current_method': trial['method'], 'per_method': totals(records)})
        check(out)
        assert len(records) == 240
        final = [audit.audit_case(out/t['name'], acceptance_override=t.get('acceptance_override')) for t in p['queue']]
        assert all(x['accepted'] for x in final)
        ex.save(out/'collection-acceptance.json', {'at': time.time(), 'accepted': True,
            'response_trials': 240, 'registered_protocol_sha256': ex.sha(out/'protocol.json'),
            'record_audits': final, 'rq1_original_collection_unchanged': True, 'rq3b_started': False,
            'operational_amendment_sha256': ex.sha(out/'amendment.json'),
            'retained_rejected_attempts_sha256': ex.sha(out/'rejected-attempts.json')})
        publish(out, {'state': 'complete', 'position': 240, 'responses_completed': 240, 'per_method': totals(records)})
        cleanup = subprocess.run(['docker', 'stop', '--time', '20', *NODES], capture_output=True, text=True, timeout=90)
        ex.save(out/'runtime-cleanup-and-retention.json', {'at': time.time(), 'containers': NODES,
            'stop_returncode': cleanup.returncode, 'stdout': cleanup.stdout, 'stderr': cleanup.stderr,
            'images_volumes_and_raw_evidence_preserved': True, 'other_project_containers_modified': False})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    out = parser.parse_args().output.resolve()
    try:
        run(out)
    except Exception as error:
        current = json.loads((out/'progress.json').read_text())
        publish(out, {**current, 'state': 'halted', 'reason': str(error)})
        raise

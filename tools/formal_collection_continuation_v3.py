"""Explicit, preserved recovery for the historical-name observer defect.

The original frozen queue, actions, models, verifier and timing remain intact.
The first failed attempt stays archived and excluded, with immutable hashes.
Only the prospectively registered historical-observer executor is substituted.
"""
import argparse
import copy
import fcntl
import hashlib
import json
import os
import shutil
import time
import traceback
from pathlib import Path

import formal_collection_v2 as original
import formal_replay_executor_v3 as executor

ROOT = original.ROOT
VERSION = 'historical-subject-absence-v3'
FILES = ['historical_business_v3.py', 'formal_replay_executor_v3.py',
         'formal_collection_continuation_v3.py', 'test_historical_business_v3.py']
BASE_CHECK = original.check
BASE_SAVE = original.save
BASE_AUDIT = original.independent_audit


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def tree_hashes(directory):
    return {str(p.relative_to(directory)): original.sha(p)
            for p in sorted(directory.rglob('*')) if p.is_file()}


def check_prefix(out, p, length):
    receipts = []
    identities = []
    for entry in p['order'][:length]:
        directory = out / 'trials' / entry['trial_id']
        record = json.loads((directory / 'record.json').read_text())
        receipt = json.loads((directory / 'audit.json').read_text())
        case = copy.deepcopy(p)
        case['trial_identity'] = entry
        recomputed = BASE_AUDIT(record, case, directory)
        if not receipt['accepted'] or recomputed != receipt:
            raise RuntimeError('Earlier accepted trace changed: ' + entry['trial_id'])
        receipts.append({'entry': entry, 'audit': receipt})
        identities.append({'trial_id': entry['trial_id'],
                           'record_sha256': original.sha(directory / 'record.json'),
                           'audit_sha256': original.sha(directory / 'audit.json')})
    return receipts, identities


def prepare(out, validation_log):
    lock = (out / 'runtime/queue.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    p = BASE_CHECK(out)
    progress = json.loads((out / 'runtime/progress.json').read_text())
    assert progress['state'] == 'halted' and progress['position'] == 76
    entry = p['order'][75]
    assert entry == progress['current'] and entry['trial_id'] == '076-P3-1-2-repair-r1'
    old_worker = json.loads((out / 'runtime/worker.json').read_text())
    try:
        os.kill(old_worker['pid'], 0)
    except ProcessLookupError:
        pass
    else:
        raise RuntimeError('Prior worker is still alive; refuse recovery')
    failed = out / 'trials' / entry['trial_id']
    record = json.loads((failed / 'record.json').read_text())
    audit = json.loads((failed / 'audit.json').read_text())
    assert not audit['accepted'] and original.sha(failed / 'record.json') == audit['raw_record_sha256']
    assert not record['action_executed'] and record['post_reset_confirmed']
    assert record['environment_after_final_reset']['passed']
    assert 'deployments.apps "web" not found' in record['collection_or_setup_error']
    assert 'q.observe(key)' in record['traceback']
    assert not record.get('reset_error') and not record.get('termination_error')
    receipts, identities = check_prefix(out, p, 75)
    assert progress['completed'][:-1] == receipts
    amendment = out / 'amendments' / VERSION
    amendment.mkdir(parents=True, exist_ok=False)
    snapshot = amendment / 'tool-snapshot'
    snapshot.mkdir()
    for name in FILES:
        (snapshot / name).write_bytes((ROOT / 'tools' / name).read_bytes())
    (amendment / 'validation.log').write_bytes(validation_log.read_bytes())
    archived = amendment / 'invalid-attempts' / (entry['trial_id'] + '-attempt-1')
    archived.parent.mkdir()
    failed_hashes = tree_hashes(failed)
    failed.rename(archived)
    assert tree_hashes(archived) == failed_hashes
    previous = amendment / 'previous-runtime'
    previous.mkdir()
    for name in ['progress.json', 'coordinator-error.json', 'cleanup-deferred.json',
                 'worker.json', 'coordinator.json', 'cleanup-worker.json']:
        source = out / 'runtime' / name
        if source.exists():
            (previous / name).write_bytes(source.read_bytes())
            if name in ['coordinator-error.json', 'cleanup-deferred.json']:
                source.unlink()
    invalid = {
        'entry': entry, 'attempt': 1, 'category': 'invalid_collector_attempt',
        'excluded_from_200_main_and_95_controls': True,
        'reason': record['collection_or_setup_error'], 'action_executed': False,
        'post_reset_confirmed': True, 'original_audit_accepted': False,
        'archive': str(archived.relative_to(ROOT)), 'file_hashes': failed_hashes,
    }
    BASE_SAVE(out / 'invalid-attempts.json', {'at': time.time(), 'attempts': [invalid]})
    value = {
        'version': VERSION, 'at': time.time(), 'first_queue_position': 76,
        'original_protocol_sha256': p['protocol_sha256'],
        'original_freeze_sha256': original.sha(out / 'freeze.json'),
        'accepted_prefix': identities, 'accepted_prefix_digest': digest(identities),
        'tool_hashes': {name: original.sha(snapshot / name) for name in FILES},
        'validation_sha256': original.sha(amendment / 'validation.log'),
        'invalid_attempt': invalid,
        'change': 'P3 historical observer records a successfully confirmed absent old Deployment '
                  'as absent/incomplete; API and probe errors still fail closed.',
        'unchanged': ['295-entry order', '40 scenarios', '5 repetitions', 'candidate actions',
                      'current verifier', 'outcome labels', 'comparison models', 'all timing',
                      'baseline', 'source lock', 'all earlier accepted raw traces'],
        'retry_policy': 'Explicitly replace this one invalid pre-action collector attempt. '
                        'No automatic retry or dropping of any other rejected attempt.',
    }
    BASE_SAVE(amendment / 'amendment.json', value)
    BASE_SAVE(out / 'runtime/continuation-amendment.json', {
        'path': str((amendment / 'amendment.json').relative_to(ROOT)),
        'sha256': original.sha(amendment / 'amendment.json'),
    })
    print(json.dumps({'prepared': True, 'preserved_accepted': 75,
                      'invalid_attempts': 1, 'resume_queue_position': 76}))


def amendment_check(out):
    p = BASE_CHECK(out)
    pointer = json.loads((out / 'runtime/continuation-amendment.json').read_text())
    path = ROOT / pointer['path']
    assert original.sha(path) == pointer['sha256'], 'Continuation amendment changed'
    amendment = json.loads(path.read_text())
    assert amendment['original_protocol_sha256'] == p['protocol_sha256']
    assert original.sha(out / 'freeze.json') == amendment['original_freeze_sha256']
    for name, identity in amendment['tool_hashes'].items():
        assert original.sha(ROOT / 'tools' / name) == identity, 'Continuation source changed: ' + name
        assert original.sha(path.parent / 'tool-snapshot' / name) == identity
    assert original.sha(path.parent / 'validation.log') == amendment['validation_sha256']
    archived = ROOT / amendment['invalid_attempt']['archive']
    assert tree_hashes(archived) == amendment['invalid_attempt']['file_hashes']
    for item in amendment['accepted_prefix']:
        directory = out / 'trials' / item['trial_id']
        assert original.sha(directory / 'record.json') == item['record_sha256']
        assert original.sha(directory / 'audit.json') == item['audit_sha256']
    p['collector_amendment_sha256'] = pointer['sha256']
    return p


def run(out):
    p = amendment_check(out)
    pointer = json.loads((out / 'runtime/continuation-amendment.json').read_text())
    audit_source = BASE_AUDIT

    def amended_audit(record, case, path):
        result = audit_source(record, case, path)
        if record.get('collector_amendment_sha256') != pointer['sha256']:
            result['failures'].append('collector amendment identity')
        for sample in record.get('samples', []):
            historical = sample.get('historical_business_check', {})
            if historical.get('historical_subject_absent'):
                if (record['candidate'] != 'P3' or historical.get('healthy') is not False
                        or historical.get('rollout', {}).get('complete') is not False
                        or historical.get('rollout', {}).get('subject_present') is not False):
                    result['failures'].append('historical absent-subject diagnostic')
        result['accepted'] = not result['failures']
        result['collector_amendment_sha256'] = pointer['sha256']
        return result

    def amended_save(path, value):
        if path.name in ['progress.json', 'worker.json', 'collection-acceptance.json']:
            value = dict(value, collector_amendment_sha256=pointer['sha256'])
        if path.name == 'progress.json':
            accepted = [x for x in value.get('completed', []) if x['audit']['accepted']]
            value['main_completed'] = sum(x['entry']['category'] == 'main' for x in accepted)
            value['controls_completed'] = sum(x['entry']['category'] != 'main' for x in accepted)
            value['invalid_attempts'] = 1
        BASE_SAVE(path, value)

    # Delegate the original queue and all original acceptance predicates. These
    # explicit substitutions live only in this registered continuation process.
    original.check = amendment_check
    original.executor = executor
    original.independent_audit = amended_audit
    original.save = amended_save
    BASE_SAVE(out / 'runtime/coordinator.json', {'pid': os.getpid(), 'at': time.time(),
              'mode': VERSION, 'protocol_sha256': p['protocol_sha256'],
              'collector_amendment_sha256': pointer['sha256']})
    try:
        original.run(out)
    except Exception as error:
        BASE_SAVE(out / 'runtime/coordinator-error.json', {
            'at': time.time(), 'error': str(error), 'traceback': traceback.format_exc(),
            'collector_amendment_sha256': pointer['sha256'],
        })
        progress = json.loads((out / 'runtime/progress.json').read_text())
        progress.update(state='halted', at=time.time(), continuation_error=str(error))
        amended_save(out / 'runtime/progress.json', progress)
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--prepare', action='store_true')
    ap.add_argument('--validation-log', type=Path)
    args = ap.parse_args()
    out = args.output.resolve()
    if args.prepare:
        assert args.validation_log and args.validation_log.is_file()
        prepare(out, args.validation_log)
    else:
        run(out)


if __name__ == '__main__':
    main()

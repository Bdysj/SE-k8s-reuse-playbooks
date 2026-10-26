"""Planning-only worker with a real parent deadline and externally managed tokens."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from offline_work.rq3b_api_v1.client import request, checked_candidate, save, MODEL
from offline_work.rq3b_api_v1.pilot import responses_payload, SCHEMA, SOURCE


def worker(path):
    payload = json.loads(path.read_text())
    request(path.parent / 'transport', payload, timeout=180)


def plan(directory, envelope, remaining):
    directory.mkdir(parents=True, exist_ok=False)
    save(directory / 'permitted-input.json', envelope)
    payload = responses_payload(MODEL, json.dumps(envelope, sort_keys=True), 2048, effort='high', schema=SCHEMA)
    payload['instructions'] = (SOURCE / 'instructions.txt').read_text()
    path = directory / 'payload.json'; save(path, payload)
    cap = min(180, remaining)
    started = time.monotonic()
    record = {'model': MODEL, 'requested_max_output_tokens': 2048,
              'token_limit_control': 'user-managed backend; not client-verified',
              'client_token_boundary_gate_waived_by_user': True,
              'total_wall_deadline_seconds': cap, 'client_retries': 0,
              'gateway_internal_retries_verified': False, 'generated_plan_executed': False,
              'tool_call_allowance': 0, 'started_at': time.time(), 'accepted': False}
    with (directory / 'worker.log').open('w') as log:
        child = subprocess.Popen([sys.executable, '-B', '-m', 'offline_work.rq3b_live_v1.api',
                                  '--worker', str(path)], stdout=log, stderr=subprocess.STDOUT,
                                  start_new_session=True)
        try:
            child.wait(timeout=cap)
        except subprocess.TimeoutExpired:
            record['deadline_reached'] = True
            os.killpg(child.pid, signal.SIGTERM)
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL); child.wait()
            record['remote_generation_cancelled_verified'] = False
        record['returncode'] = child.returncode
    record['elapsed_seconds'] = time.monotonic() - started
    try:
        if record.get('deadline_reached') or child.returncode or record['elapsed_seconds'] > cap:
            raise ValueError('Model call did not finish within its deadline')
        transport = json.loads((directory / 'transport/receipt.json').read_text())
        if not transport['http_completed']: raise ValueError('HTTP response unavailable')
        response = json.loads((directory / 'transport/response.json').read_text())
        # None explicitly removes CLIENT token admission. Byte/schema/tool/identity checks remain.
        value = checked_candidate(response, MODEL, None)
        record['usage'] = response.get('usage')
        record['accepted'] = True
        save(directory / 'checked-output.json', value)
        (directory / 'plan.yaml').write_text(value['plan_yaml'])
    except Exception as error:
        record['error'] = str(error)
    save(directory / 'bounded-call.json', record)
    if not record['accepted']: raise RuntimeError(record.get('error', 'Model output rejected'))
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--worker', type=Path, required=True)
    worker(parser.parse_args().worker)

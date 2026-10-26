"""Operational sequencing: preserve pilot rejection, corrected pilots, audit, registration, collection."""
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

import reuse_flow_executor as ex

PREPARATION = ex.ROOT/'results/RQ3a-response-preparation-v2'
RUNTIME = PREPARATION/'runtime'
PILOTS = ex.ROOT/'results/RQ3a-controller-pilots-v2'
FORMAL = ex.ROOT/'results/formal-rq3a-responses-v1'


def phase(name, **extra):
    ex.save(RUNTIME/'pipeline.json', {'at': time.time(), 'stage': name, **extra})
    if name != 'formal_collection' and not FORMAL.exists():
        progress = json.loads((PREPARATION/'progress.json').read_text())
        progress.update(at=time.time(), state='preparing', stage=name,
                        controller_pilot_progress=str((PILOTS/'progress.json').relative_to(ex.ROOT)),
                        pipeline_status=str((RUNTIME/'pipeline.json').relative_to(ex.ROOT)))
        ex.save(PREPARATION/'progress.json', progress)


def command(script, args, logfile=None):
    call = [sys.executable, '-u', str(ex.ROOT/'tools'/script), *args]
    if logfile:
        with logfile.open('x') as stream:
            result = subprocess.run(call, cwd=ex.ROOT, stdout=stream, stderr=subprocess.STDOUT)
    else:
        result = subprocess.run(call, cwd=ex.ROOT)
    if result.returncode:
        raise RuntimeError(script+' halted with status '+str(result.returncode))


def main():
    unit = RUNTIME/'registration-unit-acceptance.json'
    tested = json.loads(unit.read_text())
    assert tested['accepted']
    assert tested['source_sha256'] == ex.sha(ex.ROOT/'tools/prepare_rq3a_registered_responses.py')
    phase('waiting_for_preserved_rejected_pilot_reset')
    previous = ex.ROOT/'results/RQ3a-controller-pilots-v1/progress.json'
    deadline = time.monotonic()+1800
    while json.loads(previous.read_text())['state'] != 'halted':
        if time.monotonic() >= deadline:
            raise RuntimeError('Prior pilot did not finish and release its reset boundary within30min')
        time.sleep(5)
    phase('preparing_corrected_controller_pilots')
    command('prepare_rq3a_response_pilots_v2.py', ['--output', str(PILOTS)])
    phase('corrected_live_controller_pilots')
    command('run_rq3a_response_pilots.py', ['--output', str(PILOTS)], PILOTS/'worker.log')
    phase('independent_controller_pilot_audit')
    command('audit_rq3a_response_pilots.py', ['--output', str(PILOTS)], PILOTS/'independent-audit.log')
    phase('registering_240_method_controlled_responses')
    command('prepare_rq3a_registered_responses.py', ['--output', str(FORMAL), '--pilots', str(PILOTS)])
    phase('formal_collection', formal_progress=str((FORMAL/'progress.json').relative_to(ex.ROOT)),
          protocol_sha256=ex.sha(FORMAL/'protocol.json'))
    command('run_rq3a_registered_responses.py', ['--output', str(FORMAL)], FORMAL/'worker.log')
    phase('complete', formal_progress=str((FORMAL/'progress.json').relative_to(ex.ROOT)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        ex.save(RUNTIME/'pipeline.json', {'at': time.time(), 'stage': 'halted', 'reason': str(error),
                                        'traceback': traceback.format_exc()})
        progress = json.loads((PREPARATION/'progress.json').read_text())
        progress.update(at=time.time(), state='halted', reason=str(error))
        ex.save(PREPARATION/'progress.json', progress)
        raise

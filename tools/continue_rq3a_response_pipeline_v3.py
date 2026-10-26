"""Continue a scoped reset retry, then independently audit and register240 responses."""
import json
import subprocess
import sys
import time
import traceback

import reuse_flow_executor as ex

PREPARATION = ex.ROOT/'results/RQ3a-response-preparation-v2'
RUNTIME = PREPARATION/'runtime/reset-retry-v3'
PILOTS = ex.ROOT/'results/RQ3a-controller-pilots-v3'
FORMAL = ex.ROOT/'results/formal-rq3a-responses-v1'


def phase(name, **extra):
    value = {'at': time.time(), 'stage': name, **extra}
    ex.save(RUNTIME/'pipeline.json', value)
    ex.save(PREPARATION/'runtime/pipeline.json', value)
    if name != 'formal_collection' and not FORMAL.exists():
        progress = json.loads((PREPARATION/'progress.json').read_text())
        progress.pop('reason', None)
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
    tested = json.loads((RUNTIME/'registration-unit-acceptance.json').read_text())
    assert tested['accepted']
    assert tested['source_sha256'] == ex.sha(ex.ROOT/'tools/prepare_rq3a_registered_responses.py')
    assert not FORMAL.exists(), 'Never replace an existing formal source/protocol freeze'
    phase('preparing_scoped_reset_retry')
    command('prepare_rq3a_response_pilots_v3.py', ['--output', str(PILOTS)])
    phase('corrected_live_controller_pilots')
    command('run_rq3a_response_pilots_v3.py', ['--output', str(PILOTS)], PILOTS/'worker.log')
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
        value = {'at': time.time(), 'stage': 'halted', 'reason': str(error),
                 'traceback': traceback.format_exc()}
        ex.save(RUNTIME/'pipeline.json', value)
        ex.save(PREPARATION/'runtime/pipeline.json', value)
        progress = json.loads((PREPARATION/'progress.json').read_text())
        progress.update(at=time.time(), state='halted', reason=str(error))
        ex.save(PREPARATION/'progress.json', progress)
        raise

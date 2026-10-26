"""Run a prospectively registered cleanup amendment without changing old freezes."""
import json
import subprocess
import sys
import time
import traceback

import reuse_flow_executor as ex
import run_rq3a_registered_responses_v2 as runner

PREPARATION = ex.ROOT/'results/RQ3a-response-preparation-v2'
RUNTIME = PREPARATION/'runtime/formal-cleanup-amendment-v2'
FORMAL = ex.ROOT/'results/formal-rq3a-responses-v2'


def phase(name, **extra):
    value = {'at': time.time(), 'stage': name, **extra}
    ex.save(RUNTIME/'pipeline.json', value)
    ex.save(PREPARATION/'runtime/pipeline.json', value)
    if name == 'registering_prospective_logical_cleanup_amendment':
        progress = json.loads((PREPARATION/'progress.json').read_text())
        progress.pop('reason', None)
        progress.update(at=time.time(), state='preparing', stage=name)
        ex.save(PREPARATION/'progress.json', progress)


def main():
    assert not FORMAL.exists(), 'Never overwrite an existing formal protocol'
    phase('registering_prospective_logical_cleanup_amendment')
    call = [sys.executable, '-u', str(ex.ROOT/'tools/prepare_rq3a_registered_responses_v2.py'),
            '--output', str(FORMAL)]
    result = subprocess.run(call, cwd=ex.ROOT)
    if result.returncode:
        raise RuntimeError('Prospective response amendment registration failed')
    runner.check(FORMAL)
    phase('formal_collection', formal_progress=str((FORMAL/'progress.json').relative_to(ex.ROOT)),
          protocol_sha256=ex.sha(FORMAL/'protocol.json'))
    with (FORMAL/'worker.log').open('x') as stream:
        result = subprocess.run([sys.executable, '-u',
            str(ex.ROOT/'tools/run_rq3a_registered_responses_v2.py'), '--output', str(FORMAL)],
            cwd=ex.ROOT, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError('Response collection halted; original physical records retained')
    phase('complete', formal_progress=str((FORMAL/'progress.json').relative_to(ex.ROOT)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        phase('halted', reason=str(error), traceback=traceback.format_exc())
        path = FORMAL/'progress.json' if (FORMAL/'progress.json').exists() else PREPARATION/'progress.json'
        progress = json.loads(path.read_text())
        progress.update(at=time.time(), state='halted')
        progress.setdefault('reason', str(error))
        ex.save(PREPARATION/'progress.json', progress)
        raise

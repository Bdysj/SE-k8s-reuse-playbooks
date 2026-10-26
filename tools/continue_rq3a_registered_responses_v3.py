"""Continue the fixed queue after a read-only, refusal-aware audit amendment."""
import json
import subprocess
import sys
import time
import traceback

import reuse_flow_executor as ex
import run_rq3a_registered_responses_v3 as runner

PREPARATION = ex.ROOT/'results/RQ3a-response-preparation-v2'
RUNTIME = PREPARATION/'runtime/formal-refusal-audit-amendment-v3'
FORMAL = ex.ROOT/'results/formal-rq3a-responses-v3'


def phase(name, **extra):
    value = {'at': time.time(), 'stage': name, **extra}
    ex.save(RUNTIME/'pipeline.json', value)
    ex.save(PREPARATION/'runtime/pipeline.json', value)
    if name == 'registering_refusal_audit_amendment':
        progress = json.loads((PREPARATION/'progress.json').read_text())
        progress.pop('reason', None)
        progress.update(at=time.time(), state='preparing', stage=name)
        ex.save(PREPARATION/'progress.json', progress)


def main():
    if not FORMAL.exists():
        phase('registering_refusal_audit_amendment')
        result = subprocess.run([sys.executable, '-u',
            str(ex.ROOT/'tools/prepare_rq3a_registered_responses_v3.py'), '--output', str(FORMAL)],
            cwd=ex.ROOT)
        if result.returncode:
            raise RuntimeError('Refusal-aware audit amendment registration failed')
    runner.check(FORMAL)
    phase('formal_collection', formal_progress=str((FORMAL/'progress.json').relative_to(ex.ROOT)),
          protocol_sha256=ex.sha(FORMAL/'protocol.json'), fresh_execution_starts_at=40,
          preserved_response39_replayed=False)
    with (FORMAL/'worker.log').open('x') as stream:
        result = subprocess.run([sys.executable, '-u',
            str(ex.ROOT/'tools/run_rq3a_registered_responses_v3.py'), '--output', str(FORMAL)],
            cwd=ex.ROOT, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        progress = json.loads((FORMAL/'progress.json').read_text())
        raise RuntimeError('Response collection halted: '+str(progress.get('reason', 'see retained record')))
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

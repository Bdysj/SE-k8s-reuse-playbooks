"""Freeze supplemental RQ2 observation mappings before live development resumes."""
import copy
import time

import main_executor_v2 as previous
import main_executor_v3 as executor


def prepare():
    root=executor.ROOT
    parent=root/'results/pre-main-development-v2'
    protocol=copy.deepcopy(previous.inputs(parent))
    out=root/'results/pre-main-development-v3'
    out.mkdir(exist_ok=False)
    protocol.update(version='pre-main-development-v3',written_at=time.time(),
        predecessor_protocol=str((parent/'protocol.json').relative_to(root)),
        predecessor_sha256=executor.sha(parent/'protocol.json'),
        source_version_reason='Supplemental MySQL Service/account and conservative scheduler mappings; '
            'policy observation occurs before the final synchronous pre-action non-target check.')
    protocol['observation_scope']=('Read-only named namespace graph and admitted discovery; P2 existing '
        'MySQL Pod SELECT via Service/application account; P3 node labels/allocatable/Ready/pressure '
        'and all-namespace scheduling resource fields. Unmapped cases remain Unknown. '
        'No new helper creation, target locator or frozen candidate input substitution.')
    executor.save(out/'protocol.json',protocol)
    executor.freeze(out)
    print('Supported development v3 prepared; no live trial begun; main remains locked.')


if __name__=='__main__':
    prepare()

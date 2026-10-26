"""Prospective executor/common-gate pilots, independent of main outcomes."""
import copy
import json
import time

import yaml
import main_executor as original
import main_executor_v2 as executor


def prepare():
    root = executor.ROOT
    parent = root/'results/pre-main-development-v1'
    protocol = copy.deepcopy(original.inputs(parent))
    out = root/'results/pre-main-development-v2'
    out.mkdir(exist_ok=False)
    protocol.update(version='pre-main-development-v2', written_at=time.time(),
        predecessor_protocol='results/pre-main-development-v1/protocol.json',
        predecessor_sha256=executor.sha(parent/'protocol.json'),
        source_version_reason='Shared scope/code/bounded-action gate at the actual execution boundary; '
            'readonly assessor observations do not suppress RQ1 replays.')
    protocol['common_gate_policy'] = ('Same frozen code, isolated contexts and namespace, declared action '
        'allowlist, P2 post-action replicas within 0..4, action timeout 120s, fresh observations '
        'and confirmed reset. A gate block is recorded separately from policy and repair outcomes.')
    # A prospectively declared distinct pilot, not any of the forty main variants.
    positive = copy.deepcopy(next(x for x in protocol['controls'] if x['candidate']=='P2'))
    positive.update(id='DEV-P2-minimum1', purpose='executor and labelled development pilot',
                    excluded_from_main_matrix=True)
    negative = copy.deepcopy(positive)
    negative.update(id='DEV-P2-minimum4', purpose='distinct stricter development verifier pilot')
    negative['construction']['required_replicas'] = 4
    documents = list(yaml.safe_load_all((root/protocol['manifest']).read_text()))
    ratings = next(d for d in documents if d and d['kind']=='Deployment' and d['metadata']['name']=='ratings')
    ratings['spec']['replicas'] = 4
    path = out/'DEV-P2-minimum4.healthy.yaml'
    path.write_text(yaml.safe_dump_all(documents, sort_keys=False, width=4096))
    negative.update(healthy_manifest=str(path.relative_to(root)), manifest_sha256=executor.sha(path))
    protocol['development_pilots'] = [positive, negative]
    protocol['controls'].extend([positive, negative])
    protocol['pilot_label_policy'] = ('Use actual complete necessary-action, current-owner recovery and '
        'pre/post named regression records only. No main trial labels or expected effects. '
        'Pilot incidents contain observed workload names, symptoms and error codes, never injected '
        'fault or declared mutation labels. The two P2 pilots are a minimum implementation check, '
        'not comprehensive threshold training or a comparison conclusion.')
    executor.save(out/'protocol.json', protocol)
    executor.freeze(out)
    print('Prospective v2 frozen; two distinct development pilots; main remains locked.')


if __name__ == '__main__':
    prepare()

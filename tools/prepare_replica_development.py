"""Prospectively freeze a current-v3, isolated P2 capacity/fidelity pilot."""
import copy
import json
import time

import yaml
import main_executor_v4 as ex
import reset_baseline as rb

OUT=ex.ROOT/'results/replica-development-v1'
ROLE_MAP={'k3d-lab-'+role:'k3d-lab-validation-'+role
          for role in ('server-0','agent-0','agent-1')}


def translate(value):
    if isinstance(value,dict):return {translate(k):translate(v) for k,v in value.items()}
    if isinstance(value,list):return [translate(v) for v in value]
    return ROLE_MAP.get(value,value) if isinstance(value,str) else value


def prepare():
    parent=ex.ROOT/'results/pre-main-development-v4'
    original=ex.inputs(parent)
    OUT.mkdir(exist_ok=False)
    manifest=ex.ROOT/original['manifest']
    documents=[d for d in yaml.safe_load_all(manifest.read_text()) if d]
    path=OUT/'qualification.v3.replica.yaml'
    path.write_text(yaml.safe_dump_all(translate(documents),sort_keys=False,width=4096))
    rb.MANIFEST=path
    path.with_suffix('.fingerprint.json').write_text(json.dumps(rb.declared_fingerprint(),indent=2)+'\n')
    row=copy.deepcopy(next(x for x in original['scenarios'] if x['id']=='P2-2-1'))
    healthy=ex.ROOT/row['healthy_manifest']
    mapped=OUT/'P2-2-1.healthy.yaml'
    mapped.write_text(yaml.safe_dump_all(translate(list(yaml.safe_load_all(healthy.read_text()))),sort_keys=False,width=4096))
    row.update(healthy_manifest=str(mapped.relative_to(ex.ROOT)),manifest_sha256=ex.sha(mapped))
    protocol=copy.deepcopy(original)
    protocol.update(version='replica-development-v1',context='k3d-lab-validation',
        manifest=str(path.relative_to(ex.ROOT)),manifest_sha256=ex.sha(path),scenarios=[row],controls=[],
        written_at=time.time(),main_protocol_ready=False,main_trials=0,
        parent_protocol=str((parent/'protocol.json').relative_to(ex.ROOT)),
        parent_protocol_sha256=ex.sha(parent/'protocol.json'))
    ex.save(OUT/'protocol.json',protocol)
    ex.freeze(OUT)
    names=['prepare_replica_development.py','run_replica_development.py','audit_main_development.py',
           'ensure_replica_images.py']
    for name in names:(OUT/'tool-snapshot'/name).write_bytes((ex.ROOT/'tools'/name).read_bytes())
    plan={'type':'prospective_current_v3_replica_pilot','at':time.time(),'main_trials':0,
          'executor_protocol_sha256':ex.sha(OUT/'protocol.json'),
          'primary_protocol':str((parent/'protocol.json').relative_to(ex.ROOT)),
          'primary_protocol_sha256':ex.sha(parent/'protocol.json'),
          'role_mapping':ROLE_MAP,'candidate':'P2','scenario':'P2-2-1',
          'fault':'same frozen decrement-two/clamp-zero operator; healthy3 -> fault1 in both clusters',
          'outcome_policy':'actual independent executor flags only; no expected success label',
          'stability_seconds':300,'sample_interval_seconds':15,'warmup_seconds':30,
          'primary_monitor_interval_seconds':10,'primary_guard_timeout_seconds':30,
          'scope_limit':'P2 current-v3 fidelity and concurrent capacity only. Does not accept all '
              'forty replica constructions, train a comprehensive threshold or constitute RQ3b.',
          'fidelity_policy':'Only exact declared node-role names translated. Application '
              'names/images/resources/env/commands/volumes/Service selectors/ports/fault/criteria '
              'remain identical. Actual current-owner health, seed restoration, node facts, '
              'PVC/PV/container-volume separation and image identities checked independently.',
          'normalisation':'Different API UIDs/Service IPs/Pod names are cluster identities. '
              'Fingerprint compares declarative application contracts; Endpoint ownership is '
              'checked within each cluster. Exact hostname role translation is the only '
              'declarative normalisation. No resource or replica differences are removed.',
          'source_hashes':{name:ex.sha(ex.ROOT/'tools'/name) for name in names},
          'manifest_translation_verified':translate(documents)==list(yaml.safe_load_all(path.read_text())),
          'candidate_yaml_or_input_changed':False}
    ex.save(OUT/'pilot-plan.json',plan)
    print('Frozen current-v3 P2 replica pilot; main remains zero.')


if __name__=='__main__':prepare()

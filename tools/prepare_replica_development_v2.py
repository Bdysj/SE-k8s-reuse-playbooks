"""Retry the unchanged pilot after an independently verified PVC bootstrap."""
import json
import subprocess
import time

import yaml

import main_executor_v4 as ex
import prepare_replica_development as prepare_v1


def main():
    old=ex.ROOT/'results/replica-development-v1'
    bootstrap=json.loads((old/'volume-bootstrap.json').read_text())
    rejection=json.loads((old/'capacity-fidelity.json').read_text())
    if not bootstrap['passed'] or rejection['passed'] or not rejection.get('primary_post_reset_confirmed'):
        raise RuntimeError('Preserve the rejected attempt and require a confirmed primary boundary')
    attempt=json.loads((old/'P2-2-1-repair-attempt-1/record.json').read_text())
    if attempt.get('action_executed') or not attempt.get('post_reset_confirmed'):
        raise RuntimeError('Rejected setup must precede candidate and have a confirmed replica reset')
    out=ex.ROOT/'results/replica-development-v2'
    # A cold-runtime/image-import race was also observed in the first setup.
    # Cache validation is preparation, never an alteration of the replay budgets.
    checked=[]
    manifest=old/'qualification.v3.replica.yaml'
    for d in yaml.safe_load_all(manifest.read_text()):
        if not d or d['kind'] not in ('Deployment','StatefulSet'):continue
        spec=d['spec']['template']['spec'];node=spec['nodeSelector']['kubernetes.io/hostname']
        for c in spec['containers']:
            if not c['image'].startswith('ddn-eval/'):continue
            expected=subprocess.check_output(['docker','image','inspect',c['image'],'--format','{{.Id}}'],text=True).strip()
            raw=subprocess.check_output(['docker','exec',node,'crictl','--runtime-endpoint',
                'unix:///run/k3s/containerd/containerd.sock','inspecti',c['image']],text=True)
            actual=json.loads(raw)['status']['id']
            if expected!=actual:raise RuntimeError('Replica cache identity mismatch: '+c['image'])
            checked.append({'node':node,'image':c['image'],'id':actual})
    prepare_v1.OUT=out;prepare_v1.prepare()
    plan=json.loads((out/'pilot-plan.json').read_text())
    plan.update(type='prospective_current_v3_replica_pilot_retry',at=time.time(),
        unchanged_application_fault_candidate_and_window=True,
        rejection_record='results/replica-development-v1/capacity-fidelity.json',
        rejection_sha256=ex.sha(old/'capacity-fidelity.json'),
        volume_bootstrap_record='results/replica-development-v1/volume-bootstrap.json',
        volume_bootstrap_sha256=ex.sha(old/'volume-bootstrap.json'),
        prepared_current_role_image_cache=checked,
        reason='Old validation MySQL local PV belonged to a different role than current-v3. '
               'Only its disposable work copy was replaced and bound by a separate fixed-role '
               'binder Job. Complete unchanged seed reset then passed. No candidate ran in '
               'the rejected setup. Do not extend any executor/stability budget.')
    for name in ('prepare_replica_development_v2.py','run_replica_development_v2.py','rebind_replica_mysql.py'):
        (out/'tool-snapshot'/name).write_bytes((ex.ROOT/'tools'/name).read_bytes())
        plan['source_hashes'][name]=ex.sha(ex.ROOT/'tools'/name)
    ex.save(out/'pilot-plan.json',plan)


if __name__=='__main__':main()

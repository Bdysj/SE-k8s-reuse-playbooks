"""Deploy a separate ARM64 testbed and run a real P5 repair smoke experiment."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def run(args, **kwargs):
    print('+ ' + ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, text=True, **kwargs)


def ensure_images(index):
    catalog = json.loads((ROOT / 'reproduction/images.json').read_text())
    expected = {x['ref']: x['image_id'] for x in catalog['application_images']}
    expected['ddn-eval/mysql-seed:5.7'] = 'sha256:6cf04b6c61f98bc7c70f5a271d1ada0a083c249a2890cca562c2e95fd89a89fb'
    def present(ref, identity):
        result = subprocess.run(['docker', 'image', 'inspect', ref, '--format', '{{.Id}}'], capture_output=True, text=True)
        return result.returncode == 0 and result.stdout.strip() == identity
    if not all(present(ref, identity) for ref, identity in expected.items()):
        cache = ROOT / '.cache/reproduction'
        cache.mkdir(parents=True, exist_ok=True)
        for asset in [x for x in index['assets'] if x['category'] == 'images']:
            dest = cache / asset['name']
            if not dest.exists() or sha(dest) != asset['sha256']:
                partial = dest.with_suffix('.partial')
                with urllib.request.urlopen(asset['url'], timeout=60) as source, partial.open('wb') as target:
                    shutil.copyfileobj(source, target, 2 * 1024**2)
                if sha(partial) != asset['sha256']:
                    raise ValueError('Image archive checksum mismatch')
                partial.replace(dest)
        # Concatenate the compressed parts through a pipe. No extra joined file is needed.
        loader = subprocess.Popen(['docker', 'image', 'load'], stdin=subprocess.PIPE)
        decompressor = subprocess.Popen(['gzip', '-dc'], stdin=subprocess.PIPE, stdout=loader.stdin)
        loader.stdin.close()
        try:
            for name in index['image_bundle']['parts_in_order']:
                with (cache / name).open('rb') as source:
                    shutil.copyfileobj(source, decompressor.stdin, 2 * 1024**2)
            decompressor.stdin.close()
            if decompressor.wait() or loader.wait():
                raise RuntimeError('Image archive import failed')
        finally:
            if decompressor.poll() is None:
                decompressor.kill()
            if loader.poll() is None:
                loader.kill()
    if not all(present(ref, identity) for ref, identity in expected.items()):
        raise ValueError('Frozen application or seed image ID differs')
    return expected


def import_to_node(ref, node):
    print('Importing ' + ref + ' into ' + node, flush=True)
    exporter = subprocess.Popen(['docker', 'image', 'save', ref], stdout=subprocess.PIPE)
    importer = subprocess.Popen(['docker', 'exec', '-i', node, 'ctr', '--namespace', 'k8s.io', 'images', 'import', '--all-platforms', '-'], stdin=exporter.stdout, stdout=subprocess.DEVNULL)
    exporter.stdout.close()
    if importer.wait() or exporter.wait():
        raise RuntimeError('Node image import failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cluster', default='ddn-repro')
    parser.add_argument('--cleanup', action='store_true', help='Delete only the new test cluster after this run')
    parser.add_argument('--window', type=int, default=210, help='Post-repair observation seconds. The study uses 210.')
    args = parser.parse_args()
    if not re.fullmatch(r'ddn-repro(?:-[a-z0-9-]+)?', args.cluster):
        raise ValueError('Use a new cluster name starting with ddn-repro')
    if args.window < 210:
        raise ValueError('The verification window must be at least 210 seconds')
    for name in ('docker', 'k3d', 'kubectl', 'helm', 'gzip'):
        if not shutil.which(name):
            raise ValueError('Install required live tool: ' + name)
    info = json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .}}']))
    if info['Architecture'] not in ('arm64', 'aarch64'):
        raise ValueError('This frozen application testbed needs ARM64 Docker. The offline result image supports AMD64 too.')
    if info['MemTotal'] < 7 * 1024**3:
        raise ValueError('Give Docker at least 7 GiB RAM')
    if shutil.disk_usage(ROOT).free < 10 * 1024**3:
        raise ValueError('Keep at least 10 GiB free before creating the separate testbed')
    clusters = json.loads(subprocess.check_output(['k3d', 'cluster', 'list', '-o', 'json']))
    if any(x['name'] == args.cluster for x in clusters):
        raise ValueError('Cluster already exists. Preserve it and choose a new --cluster name.')
    output = ROOT / 'outputs' / ('live-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True)
    index = json.loads((ROOT / 'reproduction/release-assets.json').read_text())
    images = ensure_images(index)
    context = 'k3d-' + args.cluster
    manifest = output / 'qualification.yaml'
    original = ROOT / 'manifests/qualification.v3.yaml'
    docs = list(yaml.safe_load_all(original.read_text()))
    placements = {}
    for doc in docs:
        if doc and doc['kind'] in ('Deployment', 'StatefulSet'):
            spec = doc['spec']['template']['spec']
            spec['nodeSelector']['kubernetes.io/hostname'] = spec['nodeSelector']['kubernetes.io/hostname'].replace('k3d-lab-', context + '-')
            placements[doc['metadata']['name']] = (spec['nodeSelector']['kubernetes.io/hostname'], spec['containers'][0]['image'])
    manifest.write_text(yaml.safe_dump_all(docs, sort_keys=False))
    kubeconfig = output / 'private-kubeconfig'
    created = False
    receipt = {'accepted': False, 'scope': 'fresh-cluster-P5-smoke', 'formal_trials_repeated': 0,
               'cluster': args.cluster, 'context': context, 'source_manifest_sha256': sha(original),
               'rendered_manifest_sha256': sha(manifest), 'placement_change': 'Only the cluster name in hostname selectors changes.',
               'candidate': 'P5', 'candidate_sha256': sha(ROOT / 'candidates/P5.yaml'),
               'image_ids': images, 'new_model_calls': 0, 'observation_seconds': args.window}
    try:
        run(['k3d', 'cluster', 'create', args.cluster, '--servers', '1', '--agents', '2', '--image', 'rancher/k3s:v1.33.5-k3s1', '--api-port', '127.0.0.1:0', '--k3s-arg', '--disable=traefik@server:0', '--kubeconfig-update-default=false', '--kubeconfig-switch-context=false', '--timeout', '240s'])
        created = True
        kubeconfig.write_bytes(subprocess.check_output(['k3d', 'kubeconfig', 'get', args.cluster]))
        kubeconfig.chmod(0o600)
        os.environ['KUBECONFIG'] = str(kubeconfig)
        run(['kubectl', '--context', context, 'wait', '--for=condition=Ready', 'nodes', '--all', '--timeout=180s'])
        for name, (node, ref) in placements.items():
            if ref in images:
                import_to_node(ref, node)
        # The seed/clear jobs can be scheduled on any node before a PVC binds.
        for node in [context + '-server-0', context + '-agent-0', context + '-agent-1']:
            import_to_node('ddn-eval/mysql-seed:5.7', node)
        sys.path.insert(0, str(ROOT / 'tools'))
        import reset_baseline as rb
        rb.CONTEXT, rb.MANIFEST = context, manifest
        rb.FINGERPRINT = manifest.with_suffix('.fingerprint.json')
        rb.FINGERPRINT.write_text(json.dumps(rb.declared_fingerprint(), indent=2))
        with (output / 'baseline.log').open('w') as log:
            run([sys.executable, '-B', ROOT / 'tools/reset_baseline.py', '--context', context, '--manifest', manifest], stdout=log, stderr=subprocess.STDOUT)
        receipt['baseline_confirmed'] = True
        rb.ensure_probe()
        rb.kubectl('patch', 'service', 'web', '--type=merge', '-p', '{"spec":{"selector":{"service":"deliberately-missing-reproduction-target"}}}')
        time.sleep(5)
        fault_status, fault_body = rb.http_check('/')
        receipt['fault_status'] = fault_status
        if fault_status == 200:
            raise ValueError('Injected routing fault did not cause a visible failure')
        ansible = Path(sys.executable).parent / 'ansible-playbook'
        if not ansible.exists():
            raise ValueError('Install requirements.txt into this Python environment')
        with (output / 'p5-ansible.log').open('w') as log:
            run([ansible, '-i', ROOT / 'tools/ansible-inventory.ini', ROOT / 'candidates/P5.yaml', '-e', 'kube_context=' + context], stdout=log, stderr=subprocess.STDOUT)
        samples = []
        started = time.monotonic()
        while True:
            checks = []
            for name, path, status, pattern in rb.CHECKS:
                actual, body = rb.http_check(path)
                checks.append({'name': name, 'status': actual, 'content_matches': re.search(pattern, body, re.S) is not None,
                               'passed': actual == status and re.search(pattern, body, re.S) is not None})
            samples.append({'elapsed': time.monotonic() - started, 'checks': checks})
            (output / 'verification-samples.json').write_text(json.dumps(samples, indent=2))
            if not all(x['passed'] for x in checks):
                raise ValueError('Post-repair business verification failed')
            if time.monotonic() - started >= args.window:
                break
            time.sleep(5)
        with (output / 'final-baseline.log').open('w') as log:
            run([sys.executable, '-B', ROOT / 'tools/reset_baseline.py', '--context', context, '--manifest', manifest, '--verify'], stdout=log, stderr=subprocess.STDOUT)
        receipt.update(accepted=True, final_baseline_confirmed=True, verification_samples=len(samples),
                       measured_window_seconds=samples[-1]['elapsed'])
        print('PASS: fresh deployment, original database seed, injected routing failure and unchanged P5 repair.', flush=True)
    except Exception as error:
        receipt['error'] = str(error)
        raise
    finally:
        if created:
            with (output / 'final-pods.json').open('w') as log:
                subprocess.run(['kubectl', '--context', context, '-n', 'robot-shop', 'get', 'pods', '-o', 'json'], stdout=log, stderr=subprocess.DEVNULL)
            if args.cleanup:
                run(['k3d', 'cluster', 'delete', args.cluster])
                kubeconfig.unlink(missing_ok=True)
        (output / 'acceptance.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print('Live receipt: ' + str(output / 'acceptance.json'), flush=True)


if __name__ == '__main__':
    main()

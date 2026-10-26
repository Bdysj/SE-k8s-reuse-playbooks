"""Report installed dependencies and optional live-environment readiness."""
import argparse
import importlib.metadata
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {'ansible-core': '2.19.3', 'ansible-runner': '2.4.2', 'PyYAML': '6.0.3'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Read Docker and command availability. Do not start services.')
    args = parser.parse_args()
    packages = {}
    for name, version in EXPECTED.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        packages[name] = {'expected': version, 'installed': actual, 'matches': actual == version}
    data = (ROOT / 'results/formal-main-v1/protocol.json').is_file()
    release = json.loads((ROOT / 'reproduction/release-verification.json').read_text()) if (ROOT / 'reproduction/release-verification.json').exists() else {}
    answer = {'python': platform.python_version(), 'platform': platform.system(), 'architecture': platform.machine(),
              'packages': packages, 'evidence_present': data,
              'tables_ready': sys.version_info[:2] == (3, 12) and data and all(v['matches'] for v in packages.values()),
              'live_deployment_validated': release.get('fresh_live_smoke_accepted', False)}
    code = 0 if answer['tables_ready'] else 2
    if args.live:
        answer['commands'] = {name: shutil.which(name) is not None for name in ('docker', 'k3d', 'kubectl', 'helm', 'git')}
        docker_running = False
        if answer['commands']['docker']:
            try:
                result = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'],
                                        capture_output=True, text=True, timeout=15)
                docker_running = result.returncode == 0
                answer['docker_server_version'] = result.stdout.strip() if docker_running else None
            except (OSError, subprocess.TimeoutExpired):
                pass
        answer['docker_available'] = docker_running
        answer['image_release_status'] = release.get('image', 'See release verification record.')
        if not docker_running or not all(answer['commands'].values()):
            code = 2
    print(json.dumps(answer, indent=2))
    raise SystemExit(code)


if __name__ == '__main__':
    main()

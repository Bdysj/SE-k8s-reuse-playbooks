"""Run the frozen reset code with explicit placement for fresh local-path PVCs."""
import json
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import reset_baseline as baseline


class PlacedRestoreJobs:
    def __init__(self, placements):
        self.placements = placements

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, command, **kwargs):
        raw = kwargs.get('input')
        if isinstance(raw, str) and raw.startswith('{'):
            try:
                job = json.loads(raw)
            except json.JSONDecodeError:
                job = {}
            if job.get('kind') == 'Job':
                workload = {'mysql-restore': 'mysql', 'redis-restore': 'redis'}.get(job.get('metadata', {}).get('name'))
                if workload:
                    job['spec']['template']['spec']['nodeSelector'] = self.placements[workload]
                    kwargs['input'] = json.dumps(job)
        return subprocess.run(command, **kwargs)


def main():
    manifest = Path(sys.argv[sys.argv.index('--manifest') + 1])
    placements = {doc['metadata']['name']: doc['spec']['template']['spec']['nodeSelector']
                  for doc in yaml.safe_load_all(manifest.read_text())
                  if doc and doc['kind'] in ('Deployment', 'StatefulSet')}
    # A new local-path PVC binds at its first consumer. Place that consumer on
    # the application's declared node. The original retained lab already had
    # bound PVCs. All seed bytes, SQL checks and reset logic stay unchanged.
    baseline.subprocess = PlacedRestoreJobs(placements)
    raise SystemExit(baseline.main())


if __name__ == '__main__':
    main()

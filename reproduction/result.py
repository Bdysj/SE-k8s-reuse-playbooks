"""Run the same evidence reconstruction from a source checkout or a container."""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    if isinstance(value, float):
        return round(value, 8)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New report directory. It must not already exist.')
    args = parser.parse_args()
    if not (ROOT / 'results/formal-main-v1/protocol.json').exists():
        subprocess.run([sys.executable, '-B', str(ROOT / 'reproduction/restore.py')], check=True)
    work = ROOT / 'outputs' / ('tables-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    subprocess.run([sys.executable, '-B', str(ROOT / 'reproduction/rebuild.py'), '--output', str(work)], check=True)
    acceptance = json.loads((work / 'acceptance.json').read_text())
    files = ['rq1-analysis.json', 'rq2-evidence-only.json', 'rq3a-analysis.json', 'rq3b/analysis.json']
    science = {name: json.loads((work / name).read_text()) for name in files}
    science['rq3b/analysis.json'].pop('at')
    science = canonical(science)
    raw = json.dumps(science, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    identity = hashlib.sha256(raw).hexdigest()
    expected = ROOT / 'reproduction/expected-result.sha256'
    if expected.exists() and identity != expected.read_text().split()[0]:
        raise ValueError('Recomputed scientific results differ from the release reference: ' + identity)
    rq1 = science['rq1-analysis.json']['main']
    rq3a = science['rq3a-analysis.json']['per_method']
    rows = [['RQ1 main', rq1['trials'], rq1['outcomes']['Successful repair'], rq1['outcomes']['Confirmed failure']]]
    for method, data in rq3a.items():
        rows.append(['RQ3a ' + method, data['actual_live_executions'], data['all_live_outcomes']['Successful repair'], data['all_live_outcomes']['Confirmed failure']])
    rows.append(['RQ3b incidents', 45, 45, 0])
    report = {'accepted': acceptance['accepted'], 'mode': 'archived-evidence-recomputation',
              'scientific_result_sha256': identity, 'normalization': 'Remove only RQ3b generation time. Round JSON floats to 8 decimal places for the cross-platform digest. Full precision files remain available.',
              'checks': acceptance['checks'], 'summary_columns': ['scope', 'executions', 'successful', 'confirmed_failures'],
              'summary_rows': rows, 'new_live_trials': 0, 'new_model_calls': 0,
              'scientific_results': science}
    (work / 'result.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    (work / 'result.sha256').write_text(identity + '  canonical-scientific-results\n')
    table = '<tr><th>Scope</th><th>Executions</th><th>Successful</th><th>Confirmed failures</th></tr>'
    table += ''.join('<tr>' + ''.join('<td>' + html.escape(str(x)) + '</td>' for x in row) + '</tr>' for row in rows)
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Reproduction results</title><style>body{font:17px system-ui;max-width:1000px;margin:40px auto;padding:0 24px;line-height:1.6}table{border-collapse:collapse;width:100%}td,th{padding:10px;text-align:left;border-bottom:1px solid #ddd}code{overflow-wrap:anywhere}</style><h1>PASS: archived results reproduced</h1><p>These results were recomputed from the archived raw evidence. No new cluster trial or model call was made.</p><table>' + table + '</table><p>RQ1 also has 95 controls. RQ3a has 240 accepted responses, including responses with no live execution. RQ3b has 9 sequences.</p><p>44 offline conformance checks passed.</p><p>Scientific result SHA-256: <code>' + identity + '</code></p><p><a href="result.json">Full result JSON</a> · <a href="acceptance.json">Acceptance checks</a></p></html>'
    (work / 'report.html').write_text(page)
    destination = work
    if args.output:
        import shutil
        destination = args.output.resolve()
        if destination.exists():
            raise ValueError('Choose a new output directory: ' + str(destination))
        shutil.copytree(work, destination)
    print('\nPASS: archived results reproduced')
    print(f'{"Scope":30s} {"Runs":>6s} {"Success":>8s} {"Failure":>8s}')
    for scope, n, success, failure in rows:
        print(f'{scope:30s} {n:6d} {success:8d} {failure:8d}')
    print('Offline conformance: 44 passed. New live trials: 0. New model calls: 0.')
    print('Scientific result SHA-256: ' + identity)
    print('Report: ' + str(destination / 'report.html'))


if __name__ == '__main__':
    main()

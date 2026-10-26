"""Verify all inventoried evidence files and links. Read only."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=ROOT)
    args = parser.parse_args()
    data = args.data_root.resolve()
    problems, checked, count, total_bytes = [], 0, 0, 0
    rows = [json.loads(line) for line in (ROOT / 'reproduction/evidence-manifest.jsonl').read_text().splitlines()]
    rows.extend(json.loads((ROOT / 'reproduction/supplemental-manifest.json').read_text()))
    rows.extend(json.loads((ROOT / 'reproduction/runtime-log-manifest.json').read_text()))
    for row in rows:
        count += 1
        path = data / row['path']
        if not path.resolve().is_relative_to(data) or not path.is_file():
            problems.append({'path': row['path'], 'reason': 'missing_or_outside_root'})
        elif path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
            problems.append({'path': row['path'], 'reason': 'size_or_hash_mismatch'})
        else:
            checked += 1
            total_bytes += row['bytes']
    link_rows = json.loads((ROOT / 'reproduction/evidence-links.json').read_text())
    for row in link_rows:
        path = data / row['path']
        if not path.is_symlink() or str(path.readlink()) != row['target'] or not path.exists() or not path.resolve().is_relative_to(data):
            problems.append({'path': row['path'], 'reason': 'link_mismatch_or_missing_target'})
    answer = {'accepted': not problems, 'expected_regular_files': count, 'matched_regular_files': checked,
              'matched_bytes': total_bytes, 'checked_symlinks': len(link_rows),
              'problem_count': len(problems), 'first_problems': problems[:20], 'inputs_modified': False}
    print(json.dumps(answer, indent=2))
    raise SystemExit(0 if answer['accepted'] else 2)


if __name__ == '__main__':
    main()

"""Restore checked evidence assets. Existing different files are never overwritten."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(2 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='local', type=Path, help='Local directory containing the release assets')
    parser.add_argument('--seed', action='store_true', help='Also restore the disposable database seed')
    args = parser.parse_args()
    index = json.loads((ROOT / 'reproduction/release-assets.json').read_text())
    if not args.local:
        require(index['published'], 'Public downloads are not available yet. Use --from with the local assets directory.')
    expected = {x['path']: x for x in (json.loads(line) for line in (ROOT / index['evidence_file_manifest']).read_text().splitlines())}
    for filename in ('supplemental-manifest.json', 'runtime-log-manifest.json'):
        expected.update({x['path']: x for x in json.loads((ROOT / 'reproduction' / filename).read_text())})
    if args.seed:
        seed_hash = (ROOT / 'seed/mysql-5.7-seed.tar.sha256').read_text().split()[0]
        expected['seed/mysql-5.7-seed.tar'] = {'sha256': seed_hash}
    links = {x['path']: x['target'] for x in json.loads((ROOT / index['evidence_link_manifest']).read_text())}
    selected = [x for x in index['assets'] if set(x['required_for']) & {'tables', 'verify'} or (args.seed and 'live' in x['required_for'])]
    receipts, created, existing = [], 0, 0
    for asset in selected:
        require(asset['format'] == 'tar+gzip', 'Unsupported evidence archive format')
        if args.local:
            archive_path = args.local.resolve() / asset['name']
        else:
            require(asset.get('published') and (asset.get('url') or '').startswith('https://github.com/'), 'Missing verified public download URL')
            cache = ROOT / '.cache/reproduction'
            cache.mkdir(parents=True, exist_ok=True)
            archive_path = cache / asset['name']
            if not archive_path.exists() or sha(archive_path) != asset['sha256']:
                partial = archive_path.with_name(archive_path.name + '.partial')
                with urllib.request.urlopen(asset['url'], timeout=60) as source, partial.open('wb') as dest:
                    shutil.copyfileobj(source, dest, 2 * 1024 * 1024)
                require(sha(partial) == asset['sha256'], 'Downloaded asset hash mismatch')
                partial.replace(archive_path)
        require(archive_path.is_file(), 'Missing asset: ' + asset['name'])
        require(archive_path.stat().st_size == asset['bytes'] and sha(archive_path) == asset['sha256'], 'Asset size/hash mismatch: ' + asset['name'])
        with tarfile.open(archive_path) as archive:
            pending = []
            for member in archive:
                name = PurePosixPath(member.name)
                require(not name.is_absolute() and '..' not in name.parts, 'Unsafe archive path')
                destination = ROOT.joinpath(*name.parts)
                require(destination.resolve().is_relative_to(ROOT), 'Archive path escapes repository')
                if member.isfile():
                    require(member.name in expected, 'File absent from evidence manifest: ' + member.name)
                    if destination.exists() or destination.is_symlink():
                        require(destination.is_file() and not destination.is_symlink() and sha(destination) == expected[member.name]['sha256'],
                                'Preserve existing different file: ' + member.name)
                        existing += 1
                    else:
                        pending.append(member)
                elif member.issym():
                    require(links.get(member.name) == member.linkname, 'Unexpected link target')
                    target = (destination.parent / member.linkname).resolve()
                    require(target.is_relative_to(ROOT), 'Link escapes repository')
                    if destination.exists() or destination.is_symlink():
                        require(destination.is_symlink() and str(destination.readlink()) == member.linkname, 'Existing link differs')
                        existing += 1
                    else:
                        pending.append(member)
                else:
                    raise ValueError('Unexpected archive member type')
            require(shutil.disk_usage(ROOT).free > sum(m.size for m in pending) + 3 * 1024**3,
                    'Insufficient free space for evidence restoration plus a 3 GiB reserve')
            archive.extractall(path=ROOT, members=pending, filter='data')
            for member in pending:
                destination = ROOT / member.name
                if member.isfile():
                    require(sha(destination) == expected[member.name]['sha256'], 'Restored file hash mismatch')
                created += 1
        receipts.append({'name': asset['name'], 'sha256': asset['sha256'], 'restored_members': len(pending)})
        print('Restored: ' + asset['name'], flush=True)
    for name, target in links.items():
        path = ROOT / name
        require(path.is_symlink() and str(path.readlink()) == target and path.exists(), 'Missing restored link: ' + name)
    output = ROOT / 'outputs' / ('restore-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True)
    result = {'accepted': True, 'assets': receipts, 'new_members': created, 'existing_identical_members': existing,
              'symlinks_verified': len(links), 'seed_restored': args.seed, 'existing_different_files_overwritten': 0,
              'docker_commands_run': 0}
    (output / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS: ' + str(output), flush=True)


if __name__ == '__main__':
    main()

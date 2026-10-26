"""Download or copy checked evidence archives for a reproducible image build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='local', type=Path)
    args = parser.parse_args()
    index = json.loads((ROOT / 'reproduction/release-assets.json').read_text())
    output = ROOT / '.build-assets'
    output.mkdir(exist_ok=True)
    for item in index['assets']:
        if not set(item['required_for']) & {'tables', 'verify'}:
            continue
        dest = output / item['name']
        if not dest.exists() or sha(dest) != item['sha256']:
            partial = dest.with_suffix('.partial')
            if args.local:
                shutil.copyfile(args.local / item['name'], partial)
            else:
                if not item.get('published') or not item.get('url'):
                    raise ValueError('Release asset is not published')
                with urllib.request.urlopen(item['url'], timeout=60) as source, partial.open('wb') as target:
                    shutil.copyfileobj(source, target, 2 * 1024**2)
            if partial.stat().st_size != item['bytes'] or sha(partial) != item['sha256']:
                raise ValueError('Asset identity mismatch: ' + item['name'])
            partial.replace(dest)
        print('Verified build asset: ' + item['name'], flush=True)


if __name__ == '__main__':
    main()

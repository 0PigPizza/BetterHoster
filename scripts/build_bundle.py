"""Create a Windows or Linux Docker-based distribution archive."""
import argparse
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ('Dockerfile', 'compose.yaml', '.dockerignore', 'docker-entrypoint.sh',
         'requirements.txt', 'schema.sql', 'README.md')


def build(platform, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='betterhoster-bundle-') as temporary:
        folder = Path(temporary) / 'BetterHoster'; folder.mkdir()
        for name in FILES:
            shutil.copy2(ROOT / name, folder / name)
        for pattern in ('*.py', '*.html'):
            for source in ROOT.glob(pattern): shutil.copy2(source, folder / source.name)
        launcher = 'start-windows.cmd' if platform == 'windows' else 'start-linux.sh'
        shutil.copy2(ROOT / launcher, folder / launcher)
        if platform == 'windows':
            archive = destination / 'BetterHoster-Windows.zip'
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                for path in sorted(folder.rglob('*')):
                    if path.is_file(): z.write(path, path.relative_to(folder.parent))
        else:
            archive = destination / 'BetterHoster-Linux.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                tar.add(folder, arcname=folder.name, filter=lambda item: _normalize(item))
        return archive


def _normalize(item):
    if item.name.endswith(('.sh',)):
        item.mode |= 0o111
    return item


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', required=True, choices=('windows', 'linux'))
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    print(build(args.platform, args.output.resolve()))

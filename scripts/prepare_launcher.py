"""Collect the application files embedded in the executable launcher."""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / 'launcher' / 'payload'
FILES = ('Dockerfile', 'compose.yaml', '.dockerignore', 'docker-entrypoint.sh',
         'requirements.txt', 'schema.sql')


def prepare():
    PAYLOAD.mkdir(parents=True, exist_ok=True)
    sources = [ROOT / name for name in FILES]
    sources += sorted(ROOT.glob('*.py')) + sorted(ROOT.glob('*.html'))
    expected = {source.name for source in sources}
    unexpected = [path.name for path in PAYLOAD.iterdir() if path.name not in expected]
    if unexpected:
        raise RuntimeError('Unexpected files in generated launcher payload: ' + ', '.join(unexpected))
    for source in sources:
        shutil.copy2(source, PAYLOAD / source.name)
    return len(sources)


if __name__ == '__main__':
    print('Prepared', prepare(), 'application files for the launcher.')

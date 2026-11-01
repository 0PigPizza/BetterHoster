"""Search and install server-compatible Modrinth modpacks."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

import minecraft_servers as mc
import server_versions

API = 'https://api.modrinth.com/v2/'


def search(query, offset=0):
    if not isinstance(query, str) or len(query) > 100 or not isinstance(offset, int) or not 0 <= offset <= 5000:
        raise ValueError('Invalid modpack search.')
    facets = [['project_type:modpack'], ['environment:client_and_server', 'environment:server_only',
        'environment:server_only_client_optional', 'environment:dedicated_server_only',
        'environment:client_or_server', 'environment:client_or_server_prefers_both']]
    parameters = urllib.parse.urlencode({'query': query, 'facets': json.dumps(facets), 'limit': 24, 'offset': offset, 'index': 'downloads'})
    data = mc.fetch_json(API + 'search?' + parameters)
    items = []
    for item in data['hits']:
        if item.get('server_side') == 'unsupported':
            continue
        icon = item.get('icon_url') or ''
        if urllib.parse.urlsplit(icon).hostname != 'cdn.modrinth.com' or not icon.startswith('https://'):
            icon = ''
        items.append({'id': item['project_id'], 'name': item['title'], 'description': item.get('description', ''),
                      'author': item.get('author', ''), 'icon': icon, 'downloads': item.get('downloads', 0)})
    return {'modpacks': items, 'has_more': offset + data['limit'] < data['total_hits']}


def versions(project):
    if not isinstance(project, str) or not re.fullmatch(r'[A-Za-z0-9]{8}', project):
        raise ValueError('Select a modpack.')
    metadata = mc.fetch_json(API + 'project/' + project)
    if metadata.get('project_type') != 'modpack' or metadata.get('server_side') == 'unsupported':
        raise ValueError('This project is not a server-compatible modpack.')
    data = mc.fetch_json(API + 'project/' + project + '/version')
    result = []
    for item in data:
        if item.get('environment') == 'client_only' or not any(f['filename'].endswith('.mrpack') for f in item.get('files', [])):
            continue
        kind = {'release':'stable', 'beta':'beta', 'alpha':'prerelease'}.get(item.get('version_type'), 'stable')
        result.append({'id': item['id'], 'label': item['version_number'], 'type': kind})
    return result


def checked_path(base, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or '\x00' in relative:
        raise ValueError('The modpack contains an invalid path.')
    path = Path(relative)
    if path.is_absolute() or any(part in ('.', '..') for part in path.parts):
        raise ValueError('The modpack contains an invalid path.')
    target = (base / path).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError('The modpack contains an invalid path.')
    return target


def install(server_id, project, version_id):
    if not isinstance(version_id, str) or not re.fullmatch(r'[A-Za-z0-9]{8}', version_id):
        raise ValueError('Select a modpack version.')
    if not any(v['id'] == version_id for v in versions(project)):
        raise ValueError('This modpack version is no longer available.')
    release = mc.fetch_json(API + 'version/' + version_id)
    if release.get('project_id') != project:
        raise ValueError('Invalid modpack version.')
    entry = next((f for f in release['files'] if f.get('primary') and f['filename'].endswith('.mrpack')), None)
    if entry is None:
        entry = next((f for f in release['files'] if f['filename'].endswith('.mrpack')), None)
    if entry is None or not entry['url'].startswith('https://cdn.modrinth.com/'):
        raise ValueError('No supported modpack file is available.')
    folder = mc.server_dir(server_id)
    with mc.LOCK:
        original = mc.get_server(server_id)
        if original['edition'] != 'java':
            raise ValueError('Modpacks are available for Java servers.')
        if original['running']:
            raise ValueError('Stop the server before installing a modpack.')
        if original.get('software') == 'modpacks' and original.get('version') == version_id:
            raise ValueError('This modpack version is already installed.')
        base = folder / '.yamch'
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.modpack-', dir=base) as tmp:
            temp = Path(tmp)
            archive = temp / 'pack.mrpack'
            mc.download(entry['url'], archive)
            server_versions.verify(archive, 'sha512', entry['hashes']['sha512'])
            stage = temp / 'files'; stage.mkdir()
            with zipfile.ZipFile(archive) as z:
                if sum(i.file_size for i in z.infolist()) > 1024 * 1024 * 1024:
                    raise ValueError('The modpack is too large.')
                index = json.loads(z.read('modrinth.index.json'))
                if index.get('game') != 'minecraft' or index.get('formatVersion') != 1:
                    raise ValueError('This modpack format is not supported.')
                deps = index.get('dependencies', {})
                game = deps.get('minecraft')
                if not isinstance(game, str):
                    raise ValueError('The modpack does not specify a Minecraft version.')
                for entry in index.get('files', []):
                    if entry.get('env', {}).get('server', 'required') != 'required':
                        continue
                    target = checked_path(stage, entry.get('path'))
                    allowed_hosts = {'cdn.modrinth.com', 'github.com', 'raw.githubusercontent.com', 'mediafilez.forgecdn.net', 'media.forgecdn.net'}
                    urls = [url for url in entry.get('downloads', []) if urllib.parse.urlsplit(url).scheme == 'https' and urllib.parse.urlsplit(url).hostname in allowed_hosts]
                    if not urls or 'sha512' not in entry.get('hashes', {}):
                        raise ValueError('A modpack file has no supported verified download.')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    mc.download(urls[0], target)
                    server_versions.verify(target, 'sha512', entry['hashes']['sha512'])
                for prefix in ('overrides/', 'server-overrides/'):
                    for item in z.infolist():
                        if not item.filename.startswith(prefix) or item.is_dir():
                            continue
                        if (item.external_attr >> 16) & 0o170000 == 0o120000:
                            raise ValueError('The modpack contains an invalid file.')
                        target = checked_path(stage, item.filename[len(prefix):])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(item) as source, target.open('wb') as out:
                            shutil.copyfileobj(source, out)
            if 'fabric-loader' in deps: software, version, loader = 'fabric', game, deps['fabric-loader']
            elif 'quilt-loader' in deps: software, version, loader = 'quilt', game, deps['quilt-loader']
            elif 'neoforge' in deps: software, version, loader = 'neoforge', deps['neoforge'], None
            elif 'forge' in deps: software, version, loader = 'forge', game + '-' + deps['forge'], None
            else: software, version, loader = 'vanilla', game, None
            files = [p for p in stage.rglob('*') if p.is_file()]
            for source in files:
                target = checked_path(folder, source.relative_to(stage).as_posix())
                if target.exists():
                    if not target.is_file() or target.stat().st_size != source.stat().st_size:
                        raise ValueError('The modpack conflicts with an existing server file: ' + source.relative_to(stage).as_posix())
                    with target.open('rb') as a, source.open('rb') as b:
                        if hashlib.file_digest(a, 'sha256').digest() != hashlib.file_digest(b, 'sha256').digest():
                            raise ValueError('The modpack conflicts with an existing server file: ' + source.relative_to(stage).as_posix())
            original_metadata = (folder / 'dashboard.json').read_text()
            created = []
            try:
                # The same underlying loader may already be installed.
                if original.get('software', 'vanilla') == software and original['version'] == version and not loader:
                    installed = dict(original)
                else:
                    installed = server_versions.install(server_id, software, version, loader)
                for source in files:
                    target = checked_path(folder, source.relative_to(stage).as_posix())
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
                        created.append(target)
                installed.pop('running', None); installed.pop('eulaAccepted', None); installed.pop('address', None)
                installed.update({'software':'modpacks', 'version':version_id, 'version_label':release['version_number'], 'modpack_project':project, 'modpack_name':index.get('name', 'Modpack'), 'modpack_loader':software, 'modpack_game_version':game})
                replacement = folder / '.dashboard-modpack.json'
                replacement.write_text(json.dumps(installed)); os.replace(replacement, folder / 'dashboard.json')
            except Exception:
                for target in created:
                    target.unlink(missing_ok=True)
                (folder / 'dashboard.json').write_text(original_metadata)
                raise
        return installed

"""Find and install Modrinth mods and plugins compatible with a server."""
import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
import urllib.parse
from pathlib import Path

import minecraft_servers as mc

API = 'https://api.modrinth.com/v2/'
MOD_LOADERS = {'fabric': ['fabric'], 'quilt': ['quilt'], 'forge': ['forge'], 'neoforge': ['neoforge'], 'arclight': ['forge']}
PLUGIN_LOADERS = {'spigot': ['spigot', 'bukkit'], 'paper': ['paper', 'spigot', 'bukkit'], 'purpur': ['purpur', 'paper', 'spigot', 'bukkit'], 'folia': ['folia'], 'arclight': ['spigot', 'bukkit']}


def compatibility(info):
    if info.get('edition') != 'java':
        return {'mods': [], 'plugins': [], 'game_version': ''}
    software = info.get('modpack_loader', '') if info.get('software') == 'modpacks' else info.get('software', 'vanilla')
    version = info.get('modpack_game_version', '') if info.get('software') == 'modpacks' else info.get('version', '')
    # Older modpack records can identify Forge from their saved launch arguments.
    if info.get('software') == 'modpacks' and not software:
        args = info.get('launch_args', '')
        match = re.search(r'/net/minecraftforge/forge/([^/]+)/unix_args\.txt$', args)
        if match:
            software, version = 'forge', match.group(1)
        else:
            match = re.search(r'/net/neoforged/neoforge/([^/]+)/unix_args\.txt$', args)
            if match:
                software, version = 'neoforge', match.group(1)
    if software == 'forge':
        version = version.split('-')[0]
    elif software == 'neoforge' and not info.get('modpack_game_version'):
        match = re.match(r'^(\d+)\.(\d+)', version)
        if match:
            major, minor = map(int, match.groups())
            version = f'{major}.{minor}' if major >= 26 else f'1.{major}' + (f'.{minor}' if minor else '')
        else:
            version = ''
    elif software == 'arclight':
        match = re.search(r'(?:^|-)(1\.\d+(?:\.\d+)?|2\d+\.\d+(?:\.\d+)?)', version)
        version = match.group(1) if match else ''
    return {'mods': MOD_LOADERS.get(software, []), 'plugins': PLUGIN_LOADERS.get(software, []), 'game_version': version}


def settings(server_id, kind):
    info = mc.get_server(server_id)
    compatible = compatibility(info)
    if kind not in ('mods', 'plugins') or not compatible[kind] or not compatible['game_version']:
        raise ValueError('No compatible catalog is available for this server.')
    return info, compatible[kind], compatible['game_version']


def releases(project, loaders=None, game=None):
    if not isinstance(project, str) or not re.fullmatch(r'[A-Za-z0-9]{8}', project):
        raise ValueError('Select a project.')
    params = urllib.parse.urlencode({'loaders': json.dumps(loaders), 'game_versions': json.dumps([game])}) if loaders and game else ''
    entries = mc.fetch_json(API + 'project/' + project + '/version' + ('?' + params if params else ''))
    return [v for v in entries if v.get('environment') not in ('client_only', 'client_only_server_optional', 'singleplayer_only') and
            v.get('server_side') != 'unsupported' and any(f.get('filename', '').endswith('.jar') and f.get('hashes', {}).get('sha512') for f in v.get('files', []))]


def compatible_version(version, loaders, game):
    return game in version.get('game_versions', []) and bool(set(loaders) & set(version.get('loaders', [])))


def mod_versions(server_id, project, kind='mods'):
    _, loaders, game = settings(server_id, kind)
    return [{'id': v['id'], 'label': v['version_number'], 'compatible': compatible_version(v, loaders, game)}
            for v in releases(project)]


def installed_mods(server_id, kind='mods'):
    if kind not in ('mods', 'plugins'): raise ValueError('Invalid add-on type.')
    directory = mc.server_dir(server_id) / kind
    if directory.is_symlink() or not directory.is_dir():
        return {}
    found = {}
    for path in directory.glob('*.jar'):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as file:
            digest = hashlib.file_digest(file, 'sha512').hexdigest()
        found.setdefault(digest, []).append(path)
    return found


def matching_mods(versions, installed):
    matches = []
    for version in versions:
        for file in version.get('files', []):
            digest = file.get('hashes', {}).get('sha512', '').lower()
            matches.extend(installed.get(digest, []))
    return list(dict.fromkeys(matches))


def search(server_id, kind, query, offset):
    _, loaders, game = settings(server_id, kind)
    if not isinstance(query, str) or len(query) > 100 or not 0 <= offset <= 5000:
        raise ValueError('Invalid search.')
    facets = [['project_type:' + ('plugin' if kind == 'plugins' else 'mod')], ['versions:' + game], ['categories:' + loader for loader in loaders],
              ['environment:client_and_server', 'environment:server_only', 'environment:server_only_client_optional', 'environment:dedicated_server_only', 'environment:client_or_server', 'environment:client_or_server_prefers_both']]
    params = urllib.parse.urlencode({'query': query, 'facets': json.dumps(facets), 'limit': 24, 'offset': offset, 'index': 'downloads'})
    data = mc.fetch_json(API + 'search?' + params)
    hits = data['hits']
    installed = installed_mods(server_id, kind)
    def result(hit):
        versions = releases(hit['project_id'])
        candidates = [v for v in versions if compatible_version(v, loaders, game)]
        if not candidates:
            return None
        version = next((v for v in candidates if v.get('version_type') == 'release'), candidates[0])
        icon = hit.get('icon_url') or ''
        if urllib.parse.urlsplit(icon).hostname != 'cdn.modrinth.com' or not icon.startswith('https://'):
            icon = ''
        return {'id': hit['project_id'], 'name': hit['title'], 'description': hit.get('description', ''), 'author': hit.get('author', ''), 'icon': icon, 'version': version['id'], 'version_label': version['version_number'], 'installed': bool(matching_mods(versions, installed))}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        items = list(pool.map(result, hits))
    return {'items': [x for x in items if x], 'has_more': offset + data['limit'] < data['total_hits'], 'total': data['total_hits'], 'limit': data['limit']}


def install(server_id, kind, project, version_id, allow_incompatible=False):
    with mc.LOCK:
        info, loaders, game = settings(server_id, kind)
        if info['running']:
            raise ValueError('Stop the server before installing.')
        versions = releases(project)
        version = next((v for v in versions if v['id'] == version_id), None)
        if not version:
            raise ValueError('This version is not available.')
        if not compatible_version(version, loaders, game) and allow_incompatible is not True:
            raise ValueError('This mod version is not compatible with the server.')
        files = [f for f in version['files'] if f.get('filename', '').endswith('.jar') and f.get('hashes', {}).get('sha512')]
        entry = next((f for f in files if f.get('primary')), files[0])
        filename = entry['filename']
        url = entry.get('url', '')
        if Path(filename).name != filename or not re.fullmatch(r'[a-fA-F0-9]{128}', entry['hashes']['sha512']) or urllib.parse.urlsplit(url).hostname != 'cdn.modrinth.com' or not url.startswith('https://'):
            raise ValueError('Invalid download.')
        directory = mc.server_dir(server_id) / kind
        directory.mkdir(exist_ok=True)
        if directory.is_symlink():
            raise ValueError('Invalid server directory.')
        target = directory / filename
        if target.exists() or target.is_symlink():
            raise ValueError('This file already exists on the server.')
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
            path = Path(temporary.name)
        try:
            mc.download(url, path)
            with path.open('rb') as file:
                digest = hashlib.file_digest(file, 'sha512').hexdigest()
            if digest != entry['hashes']['sha512'].lower():
                raise ValueError('Download verification failed.')
            os.link(path, target)
        finally:
            path.unlink(missing_ok=True)
    return {'installed': True}


def remove_mod(server_id, project, kind='mods'):
    with mc.LOCK:
        info, loaders, game = settings(server_id, kind)
        if info['running']:
            raise ValueError('Stop the server before removing a mod.')
        matches = matching_mods(releases(project), installed_mods(server_id, kind))
        if not matches:
            raise ValueError('This mod is not installed.')
        for path in matches:
            path.unlink()
    return {'installed': False}

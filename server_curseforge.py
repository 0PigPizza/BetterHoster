"""Browse and install CurseForge mods through the public modpacks.ch API."""
import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
import urllib.parse
from pathlib import Path
import minecraft_servers as mc
import server_addons

API = 'https://api.modpacks.ch/'
HOSTS = {'edge.forgecdn.net', 'mediafilez.forgecdn.net', 'media.forgecdn.net'}


def project_id(value):
    if not isinstance(value, (str, int)) or not re.fullmatch(r'[0-9]{1,12}', str(value)):
        raise ValueError('Select a mod.')
    return str(value)


def versions(project, game=None, loader=None):
    project = project_id(project)
    suffix = '/'.join(urllib.parse.quote(str(x), safe='') for x in ((game, loader, 1) if game and loader else ('all', 1)))
    result = mc.fetch_json(API + 'public/mod/' + project + '/versions/' + suffix)
    if result.get('status') != 'success': raise ValueError('Unable to load mod versions.')
    return [v for v in result.get('versions', []) if v.get('clientonly') is False and str(v.get('name', '')).endswith('.jar')]


def compatible(item, game, loaders):
    targets = item.get('targets') or []
    games = {str(t.get('version', '')).lower() for t in targets if t.get('type') == 'game'}
    kinds = {str(t.get('name', '')).lower() for t in targets if t.get('type') == 'modloader'}
    return game.lower() in games and bool(kinds.intersection(loaders))


def registry(server_id):
    folder = mc.server_dir(server_id)
    path = folder / '.betterhoster' / 'curseforge-mods.json'
    if not path.exists():
        path = folder / '.yamch' / 'curseforge-mods.json'  # Read registries from older installations.
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict): return data
    except (OSError, ValueError): pass
    return {}


def matching(server_id, project):
    entry = registry(server_id).get(project, {})
    filename, expected = entry.get('filename', ''), entry.get('sha1', '')
    if not isinstance(filename, str) or Path(filename).name != filename or not re.fullmatch(r'[a-f0-9]{40}', str(expected)): return None
    directory = mc.server_dir(server_id) / 'mods'
    path = directory / filename
    if directory.is_symlink() or path.is_symlink() or not path.is_file(): return None
    with path.open('rb') as f:
        if hashlib.file_digest(f, 'sha1').hexdigest() != expected: return None
    return path


def search(server_id, query, page):
    _, loaders, game = server_addons.settings(server_id, 'mods')
    if not isinstance(query, str) or len(query) > 100 or not isinstance(page, int) or not 1 <= page <= 200:
        raise ValueError('Invalid search.')
    loader = loaders[0]
    segments = '/'.join(urllib.parse.quote(str(x), safe='') for x in (game, loader, 'popular', page))
    query_part = '?' + urllib.parse.urlencode({'term':query}) if query else ''
    data = mc.fetch_json(API + 'public/curseforge/mods/' + ('search/' if query else 'browse/') + segments + query_part)
    if data.get('status') != 'success': raise ValueError('Unable to search CurseForge.')
    def build(item):
        pid = project_id(item['id'])
        try:
            results = [v for v in versions(pid, game, loader) if compatible(v, game, loaders)]
            if not results: return None
            selected = next((v for v in results if v.get('type') == 'release'), results[0])
            icons = [a.get('url', '') for a in item.get('art', []) if isinstance(a, dict)]
            icon = next((u for u in icons if urllib.parse.urlsplit(u).hostname in HOSTS and u.startswith('https://')), '')
            return {'id':pid, 'name':item['name'], 'description':item.get('synopsis', ''), 'author':', '.join(a.get('name','') for a in item.get('authors', [])), 'icon':icon, 'version':str(selected['id']), 'version_label':selected.get('version', selected['name']), 'installed':bool(matching(server_id, pid))}
        except (OSError, ValueError, KeyError, TypeError): return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: items = list(pool.map(build, data.get('mods', [])))
    return {'items':[v for v in items if v], 'page':int(data.get('page', page)), 'pages':int(data.get('pages', 1))}


def available_versions(server_id, project):
    _, loaders, game = server_addons.settings(server_id, 'mods')
    first = versions(project, game, loaders[0]); more = versions(project)
    unique = {str(v['id']):v for v in first + more}
    return [{'id':str(v['id']), 'label':v.get('version', v['name']), 'compatible':compatible(v, game, loaders)} for v in unique.values()]


def save_registry(server_id, data):
    path = mc.server_dir(server_id) / '.betterhoster' / 'curseforge-mods.json'
    path.parent.mkdir(exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.curseforge-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out: json.dump(data, out)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def install(server_id, project, version_id, allow_incompatible=False):
    project = project_id(project)
    with mc.LOCK:
        info, loaders, game = server_addons.settings(server_id, 'mods')
        if info['running']: raise ValueError('Stop the server before installing.')
        if matching(server_id, project): raise ValueError('This mod is already installed.')
        all_versions = versions(project, game, loaders[0]) + versions(project)
        item = next((v for v in all_versions if str(v.get('id')) == str(version_id)), None)
        if not item: raise ValueError('This mod version is no longer available.')
        if not compatible(item, game, loaders) and allow_incompatible is not True: raise ValueError('This mod version is not compatible with the server.')
        url, name, digest = item.get('url', ''), item.get('name', ''), item.get('sha1', '')
        if not isinstance(name,str) or Path(name).name != name or not name.endswith('.jar') or not re.fullmatch(r'[a-fA-F0-9]{40}', str(digest)) or not url.startswith('https://') or urllib.parse.urlsplit(url).hostname not in HOSTS:
            raise ValueError('This mod file is not available for download.')
        directory = mc.server_dir(server_id) / 'mods'
        if directory.is_symlink(): raise ValueError('Invalid server directory.')
        directory.mkdir(exist_ok=True); target = directory / name
        if target.exists() or target.is_symlink(): raise ValueError('This file already exists on the server.')
        fd, temporary = tempfile.mkstemp(prefix='.curseforge-', dir=directory); os.close(fd); temp = Path(temporary)
        try:
            mc.download(url, temp)
            with temp.open('rb') as f:
                if hashlib.file_digest(f, 'sha1').hexdigest() != digest.lower(): raise ValueError('Download verification failed.')
            os.link(temp, target)
            try:
                data = registry(server_id); data[project] = {'filename':name, 'sha1':digest.lower()}; save_registry(server_id, data)
            except Exception:
                target.unlink(missing_ok=True); raise
        finally: temp.unlink(missing_ok=True)
    return {'ok':True}


def remove(server_id, project):
    project = project_id(project)
    with mc.LOCK:
        info, _, _ = server_addons.settings(server_id, 'mods')
        if info['running']: raise ValueError('Stop the server before removing a mod.')
        path = matching(server_id, project)
        if not path: raise ValueError('This mod is not installed or its file has changed.')
        path.unlink(); data = registry(server_id); data.pop(project, None); save_registry(server_id, data)
    return {'ok':True}

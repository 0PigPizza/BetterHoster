"""Browse and install Paper plugins from Hangar."""
import hashlib
import concurrent.futures
import json
import os
import re
import tempfile
import urllib.parse
from pathlib import Path
import minecraft_servers as mc
import server_addons

API = 'https://hangar.papermc.io/api/v1/'


def settings(server_id):
    info, _, game = server_addons.settings(server_id, 'plugins')
    if info.get('software') not in ('paper', 'purpur', 'folia'):
        raise ValueError('Hangar plugins are available for Paper-compatible servers.')
    return info, game


def slug(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value): raise ValueError('Select a plugin.')
    return value


def get_project(project):
    return mc.fetch_json(API + 'projects/' + urllib.parse.quote(slug(project), safe=''))


def versions(project):
    result = mc.fetch_json(API + 'projects/' + urllib.parse.quote(slug(project), safe='') + '/versions?limit=100')
    return [v for v in result.get('result', []) if (v.get('downloads') or {}).get('PAPER', {}).get('fileInfo')]


def compatible(version, info, game, project=None):
    if game not in (version.get('platformDependencies') or {}).get('PAPER', []): return False
    if info.get('software') == 'folia' and project is not None and 'SUPPORTS_FOLIA' not in (project.get('settings') or {}).get('tags', []): return False
    return True


def registry(server_id):
    path = mc.server_dir(server_id) / '.yamch' / 'hangar-plugins.json'
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError): return {}


def matching(server_id, project):
    entry = registry(server_id).get(slug(project), {})
    name, digest = entry.get('filename', ''), entry.get('sha256', '')
    if not isinstance(name,str) or Path(name).name != name or not re.fullmatch(r'[a-f0-9]{64}', str(digest)): return None
    directory = mc.server_dir(server_id) / 'plugins'; path = directory / name
    if directory.is_symlink() or path.is_symlink() or not path.is_file(): return None
    with path.open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != digest: return None
    return path


def save_registry(server_id, data):
    path = mc.server_dir(server_id) / '.yamch' / 'hangar-plugins.json'; path.parent.mkdir(exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.hangar-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out: json.dump(data, out)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def search(server_id, query, offset):
    info, game = settings(server_id)
    if not isinstance(query,str) or len(query) > 100 or not 0 <= offset <= 5000: raise ValueError('Invalid search.')
    params = urllib.parse.urlencode({'limit':24, 'offset':offset, 'platform':'PAPER', 'query':query})
    data = mc.fetch_json(API + 'projects?' + params)
    def build(project):
        try:
            if game not in (project.get('supportedPlatforms') or {}).get('PAPER', []): return None
            if info['software'] == 'folia' and 'SUPPORTS_FOLIA' not in (project.get('settings') or {}).get('tags', []): return None
            name = slug(project['namespace']['slug'])
            releases = [v for v in versions(name) if compatible(v, info, game, project)]
            if not releases: return None
            selected = releases[0]
            icon = project.get('avatarUrl') or ''
            if urllib.parse.urlsplit(icon).hostname not in ('hangarcdn.papermc.io', 'hangar.papermc.io') or not icon.startswith('https://'): icon = ''
            return {'id':name, 'name':project['name'], 'description':project.get('description', ''), 'author':project['namespace'].get('owner',''), 'icon':icon, 'installed':bool(matching(server_id,name)), 'version':selected['name'], 'version_label':selected['name']}
        except (OSError, ValueError, KeyError, TypeError): return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        items = [item for item in pool.map(build, data.get('result', [])) if item]
    count = data.get('pagination',{}).get('count',0)
    return {'items':items, 'total':count, 'limit':24, 'has_more':offset + 24 < count}


def available_versions(server_id, project):
    info, game = settings(server_id); meta = get_project(project)
    return [{'id':v['name'], 'label':v['name'], 'compatible':compatible(v, info, game, meta)} for v in versions(project)]


def install(server_id, project, version_id, allow_incompatible=False):
    project = slug(project)
    with mc.LOCK:
        info, game = settings(server_id)
        if info['running']: raise ValueError('Stop the server before installing.')
        if matching(server_id, project): raise ValueError('This plugin is already installed.')
        meta = get_project(project); release = next((v for v in versions(project) if v.get('name') == version_id), None)
        if not release: raise ValueError('This plugin version is no longer available.')
        if not compatible(release, info, game, meta) and allow_incompatible is not True: raise ValueError('This plugin version is not compatible with the server.')
        download = release['downloads']['PAPER']; details = download['fileInfo']; name, digest, url = details.get('name',''), details.get('sha256Hash',''), download.get('downloadUrl','')
        if not isinstance(name,str) or Path(name).name != name or not name.endswith('.jar') or not re.fullmatch(r'[a-fA-F0-9]{64}', str(digest)) or not isinstance(url,str) or not url.startswith('https://') or urllib.parse.urlsplit(url).hostname != 'hangarcdn.papermc.io':
            raise ValueError('This plugin file is not available for download.')
        directory = mc.server_dir(server_id) / 'plugins'
        if directory.is_symlink(): raise ValueError('Invalid server directory.')
        directory.mkdir(exist_ok=True); target = directory / name
        if target.exists() or target.is_symlink(): raise ValueError('This file already exists on the server.')
        fd, temporary = tempfile.mkstemp(prefix='.hangar-', dir=directory); os.close(fd); temp = Path(temporary)
        try:
            mc.download(url, temp)
            with temp.open('rb') as f:
                if hashlib.file_digest(f, 'sha256').hexdigest() != digest.lower(): raise ValueError('Download verification failed.')
            os.link(temp, target)
            try:
                data = registry(server_id); data[project] = {'filename':name, 'sha256':digest.lower()}; save_registry(server_id, data)
            except Exception: target.unlink(missing_ok=True); raise
        finally: temp.unlink(missing_ok=True)
    return {'ok':True}


def remove(server_id, project):
    project = slug(project)
    with mc.LOCK:
        info, _ = settings(server_id)
        if info['running']: raise ValueError('Stop the server before removing a plugin.')
        path = matching(server_id, project)
        if not path: raise ValueError('This plugin is not installed or its file has changed.')
        path.unlink(); data = registry(server_id); data.pop(project, None); save_registry(server_id, data)
    return {'ok':True}

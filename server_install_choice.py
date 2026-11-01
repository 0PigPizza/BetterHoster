"""Apply a version install with an explicit choice about existing files."""
import json
import os
import re
import shutil
import uuid
from pathlib import Path

import minecraft_servers as mc
import server_modpacks
import server_versions


def fix_stage_links(stage):
    root = stage.resolve()
    for path in stage.rglob('*'):
        if not path.is_symlink():
            continue
        target = path.resolve()
        if not target.is_relative_to(root):
            raise ValueError('Invalid installed server file path.')
        relative = os.path.relpath(target, path.parent)
        path.unlink()
        path.symlink_to(relative, target_is_directory=target.is_dir())


def set_properties(stage, info):
    path = stage / 'server.properties'
    content = path.read_text(encoding='utf-8') if path.is_file() else ''
    values = {'server-port': str(info['port']), 'max-players': str(info.get('players', 20))}
    if info['edition'] == 'bedrock': values['server-name'] = info['name']
    for key, value in values.items():
        pattern = re.compile(r'^' + re.escape(key) + r'\s*=.*$', re.MULTILINE)
        if pattern.search(content): content = pattern.sub(lambda _: key + '=' + value, content)
        else: content += ('' if not content or content.endswith('\n') else '\n') + key + '=' + value + '\n'
    path.write_text(content, encoding='utf-8')


def install(server_id, choice, kind, first, version):
    if choice not in ('delete', 'preserve'):
        raise ValueError('Choose what to do with the existing server files.')
    if kind not in ('version', 'modpack'):
        raise ValueError('Invalid installation.')
    with mc.LOCK:
        folder = mc.server_dir(server_id)
        info = mc.get_server(server_id)
        if info['running']: raise ValueError('Stop the server before installing a version.')
        if kind == 'version':
            current = info.get('software', 'bedrock' if info['edition'] == 'bedrock' else 'vanilla')
            if current == 'snapshot': current = 'vanilla'
            if current == first and info.get('version') == version: raise ValueError('This version is already installed.')
        elif info.get('software') == 'modpacks' and info.get('version') == version:
            raise ValueError('This modpack version is already installed.')
        install_fn = server_versions.install if kind == 'version' else server_modpacks.install
        if choice == 'preserve':
            return install_fn(server_id, first, version)

        stage_id = uuid.uuid4().hex
        stage = mc.ROOT / stage_id
        backup = mc.ROOT / ('.reset-backup-' + uuid.uuid4().hex)
        stage.mkdir()
        (stage / '.betterhoster-installing').touch()
        swapped = False
        try:
            initial = dict(info)
            for key in ('running', 'address', 'eulaAccepted', 'launch_jar', 'launch_args', 'launch_properties', 'run_directory', 'version_label', 'modpack_project', 'modpack_name', 'modpack_loader', 'modpack_game_version'):
                initial.pop(key, None)
            initial.update({'id': stage_id, 'version': '', 'software': 'bedrock' if info['edition'] == 'bedrock' else 'vanilla'})
            (stage / 'dashboard.json').write_text(json.dumps(initial))
            if (folder / 'eula.txt').is_file(): shutil.copy2(folder / 'eula.txt', stage / 'eula.txt')
            install_fn(stage_id, first, version)
            set_properties(stage, info)
            fix_stage_links(stage)
            metadata = json.loads((stage / 'dashboard.json').read_text())
            metadata['id'] = server_id
            (stage / 'dashboard.json').write_text(json.dumps(metadata))
            (stage / '.betterhoster-installing').unlink()
            folder.rename(backup)
            try:
                stage.rename(folder)
            except Exception:
                backup.rename(folder)
                raise
            swapped = True
            shutil.rmtree(backup)
            return metadata
        finally:
            if not swapped and stage.exists(): shutil.rmtree(stage)

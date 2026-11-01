"""Edit the settings of one Minecraft server."""
import json
import os
import re
import tempfile

import minecraft_servers as mc

COMMON = [
    ('gamemode', 'Game mode', 'choice', 'survival', ['survival', 'creative', 'adventure']),
    ('difficulty', 'Difficulty', 'choice', 'easy', ['peaceful', 'easy', 'normal', 'hard']),
    ('force-gamemode', 'Force game mode', 'boolean', 'false'),
    ('max-players', 'Maximum players', 'number', '20', 1, 10000),
    ('server-port', 'Port', 'number', None, 1, 65535),
    ('online-mode', 'Online authentication', 'boolean', 'true'),
    ('view-distance', 'View distance', 'number', '10', 3, 32),
    ('level-name', 'World name', 'text', 'world'),
    ('level-seed', 'World seed', 'text', ''),
]
JAVA = [
    ('motd', 'Message of the day', 'text', 'A Minecraft Server'),
    ('white-list', 'Allowlist', 'boolean', 'false'),
    ('pvp', 'Player vs player', 'boolean', 'true'),
    ('hardcore', 'Hardcore', 'boolean', 'false'),
    ('allow-flight', 'Allow flight', 'boolean', 'false'),
    ('allow-nether', 'Allow Nether', 'boolean', 'true'),
    ('enable-command-block', 'Command blocks', 'boolean', 'false'),
    ('generate-structures', 'Generate structures', 'boolean', 'true'),
    ('spawn-animals', 'Spawn animals', 'boolean', 'true'),
    ('spawn-monsters', 'Spawn monsters', 'boolean', 'true'),
    ('spawn-npcs', 'Spawn NPCs', 'boolean', 'true'),
    ('spawn-protection', 'Spawn protection', 'number', '16', 0, 100000),
    ('simulation-distance', 'Simulation distance', 'number', '10', 3, 32),
    ('player-idle-timeout', 'Player idle timeout (minutes)', 'number', '0', 0, 100000),
    ('enable-query', 'Enable query', 'boolean', 'false'),
    ('enable-status', 'Server status', 'boolean', 'true'),
]
BEDROCK = [
    ('server-name', 'Server name', 'text', 'Dedicated Server'),
    ('allow-list', 'Allowlist', 'boolean', 'false'),
    ('allow-cheats', 'Allow cheats', 'boolean', 'false'),
    ('server-portv6', 'IPv6 port', 'number', '19133', 1, 65535),
    ('tick-distance', 'Simulation distance', 'number', '4', 4, 12),
    ('player-idle-timeout', 'Player idle timeout (minutes)', 'number', '30', 0, 100000),
    ('max-threads', 'Maximum threads', 'number', '8', 0, 10000),
    ('default-player-permission-level', 'Default player permission', 'choice', 'member', ['visitor', 'member', 'operator']),
    ('texturepack-required', 'Require texture packs', 'boolean', 'false'),
    ('enable-lan-visibility', 'LAN visibility', 'boolean', 'true'),
]


def definitions(info):
    entries = COMMON + (JAVA if info['edition'] == 'java' else BEDROCK)
    result = []
    for key, label, kind, default, *extra in entries:
        if info['edition'] == 'java' and key in ('simulation-distance', 'enable-status'):
            game = info.get('modpack_game_version') or str(info.get('version', ''))
            match = re.match(r'^1\.(\d+)(?:\.(\d+))?', game)
            if match:
                version = (int(match.group(1)), int(match.group(2) or 0))
                if key == 'simulation-distance' and version < (18, 0): continue
                if key == 'enable-status' and version < (19, 4): continue
        if key == 'gamemode' and info['edition'] == 'java':
            extra = [['survival', 'creative', 'adventure', 'spectator']]
        if key == 'server-port':
            default = str(info['port'])
        if key == 'max-players':
            default = str(info.get('players', 20))
        field = {'key': key, 'label': label, 'type': kind, 'default': default}
        if kind == 'choice': field['choices'] = extra[0]
        if kind == 'number': field['min'], field['max'] = extra
        result.append(field)
    return result


def config_path(folder, info):
    path = (folder / (info.get('run_directory', '') if info['edition'] == 'bedrock' else '') / 'server.properties').resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError('Invalid server configuration path.')
    return path


def read_properties(path):
    content = path.read_text(encoding='utf-8') if path.exists() else ''
    values = {}
    for line in content.splitlines():
        match = re.match(r'^\s*([^#!\s=:]+)\s*[:=]\s*(.*)$', line)
        if match:
            values[match.group(1)] = match.group(2)
    return content, values


def get(server_id):
    info = mc.get_server(server_id)
    path = config_path(mc.server_dir(server_id), info)
    _, values = read_properties(path)
    result = definitions(info)
    for field in result:
        value = values.get(field['key'], field['default'])
        if info['edition'] == 'java' and field['key'] in ('difficulty', 'gamemode') and value.isdigit():
            mapping = ['peaceful', 'easy', 'normal', 'hard'] if field['key'] == 'difficulty' else ['survival', 'creative', 'adventure', 'spectator']
            value = mapping[int(value)] if int(value) < len(mapping) else value
        field['value'] = value
    return {'fields': result}


def save(server_id, changes):
    if not isinstance(changes, dict):
        raise ValueError('Invalid settings.')
    with mc.LOCK:
        folder = mc.server_dir(server_id)
        info = mc.get_server(server_id)
        fields = {f['key']: f for f in definitions(info)}
        if not set(changes) <= set(fields):
            raise ValueError('Invalid setting.')
        path = config_path(folder, info)
        content, current = read_properties(path)
        updates = {}
        for key, value in changes.items():
            field = fields[key]
            if not isinstance(value, str) or len(value) > 512 or '\n' in value or '\r' in value or '\0' in value:
                raise ValueError('Invalid value for ' + field['label'] + '.')
            if field['type'] == 'boolean' and value not in ('true', 'false'):
                raise ValueError('Invalid value for ' + field['label'] + '.')
            if field['type'] == 'choice' and value not in field['choices']:
                raise ValueError('Invalid value for ' + field['label'] + '.')
            if field['type'] == 'number' and (not value.isascii() or not value.isdigit() or not field['min'] <= int(value) <= field['max']):
                raise ValueError('Invalid value for ' + field['label'] + '.')
            if key == 'server-name' and ';' in value:
                raise ValueError('Server name cannot contain a semicolon.')
            if info['edition'] == 'java' and key in ('difficulty', 'gamemode') and current.get(key, '').isdigit():
                value = str(field['choices'].index(value))
            updates[key] = value
        lines = content.splitlines(keepends=True)
        for key, value in updates.items():
            pattern = re.compile(r'^\s*' + re.escape(key) + r'\s*[:=]')
            positions = [i for i, line in enumerate(lines) if pattern.match(line)]
            if positions:
                lines[positions[-1]] = key + '=' + value + '\n'
            else:
                if lines and not lines[-1].endswith(('\n', '\r')): lines[-1] += '\n'
                lines.append(key + '=' + value + '\n')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.properties-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out: out.write(''.join(lines))
            os.replace(temp, path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        metadata_changed = False
        for key, target in (('server-port', 'port'), ('max-players', 'players')):
            if key in updates:
                info[target] = int(updates[key]); metadata_changed = True
        if metadata_changed:
            for key in ('running', 'address', 'eulaAccepted'): info.pop(key, None)
            metadata = folder / 'dashboard.json'
            fd, temp = tempfile.mkstemp(prefix='.dashboard-', dir=folder)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as out: json.dump(info, out)
                os.replace(temp, metadata)
            finally:
                if os.path.exists(temp): os.unlink(temp)
    return {'ok': True}

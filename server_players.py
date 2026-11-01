"""Player management for Minecraft server processes."""
import json
import re
import uuid
from pathlib import Path

import nbtlib
import minecraft_servers as mc


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def players(server_id):
    info = mc.get_server(server_id)
    folder = mc.server_dir(server_id)
    runtime = folder / info.get('run_directory', '') if info['edition'] == 'bedrock' else folder
    online = set(mc.PLAYER_SETS.get(server_id, ())) if info['running'] else set()
    result = {}
    if info['edition'] == 'java':
        for player in read_json(folder / 'usercache.json', []):
            if isinstance(player, dict) and isinstance(player.get('name'), str) and re.fullmatch(r'[A-Za-z0-9_]{1,16}', player['name']):
                result[player['name'].lower()] = {'name': player['name'], 'uuid': player.get('uuid', ''), 'online': False, 'banned': False}
        for entry in read_json(folder / 'banned-players.json', []):
            if isinstance(entry, dict) and re.fullmatch(r'[A-Za-z0-9_]{1,16}', str(entry.get('name', ''))):
                player = result.setdefault(entry['name'].lower(), {'name':entry['name'], 'uuid': entry.get('uuid', ''), 'online': False})
                player['banned'] = True
    for name in online:
        if info['edition'] == 'java' and not re.fullmatch(r'[A-Za-z0-9_]{1,16}', name): continue
        if info['edition'] == 'bedrock' and (not name or len(name) > 64 or any(c in name for c in ('"', '\\', '\n', '\r'))): continue
        entry = result.setdefault(name.lower(), {'name':name, 'uuid':'', 'banned':False})
        entry['online'] = True
    return {'players': sorted(result.values(), key=lambda p: (not p['online'], p['name'].casefold())), 'edition':info['edition'], 'running':info['running']}


def get_player(server_id, name):
    if not isinstance(name, str): raise ValueError('Select a player.')
    player = next((p for p in players(server_id)['players'] if p['name'] == name), None)
    if not player: raise ValueError('Player not found.')
    return player


def command(server_id, name, action, value=None):
    info = mc.get_server(server_id)
    if not info['running']: raise ValueError('Start the server to manage players.')
    player = get_player(server_id, name)
    target = name if info['edition'] == 'java' else '"' + name + '"'
    if action == 'kick':
        if not player['online']: raise ValueError('Player is not online.')
        text = 'kick ' + target
    elif action == 'ban' and info['edition'] == 'java': text = 'ban ' + target
    elif action == 'unban' and info['edition'] == 'java': text = 'pardon ' + target
    elif action in ('op', 'deop'): text = action + ' ' + target
    elif action == 'gamemode':
        if not player['online']: raise ValueError('Player is not online.')
        allowed = ['survival', 'creative', 'adventure'] + (['spectator'] if info['edition'] == 'java' else [])
        if value not in allowed: raise ValueError('Choose a valid game mode.')
        version = info.get('modpack_game_version') or str(info.get('version', ''))
        if info['edition'] == 'java' and re.match(r'^1\.(?:[0-9]|1[0-2])(?:\.|$)', version): value = str(allowed.index(value))
        text = 'gamemode ' + value + ' ' + target
    else: raise ValueError('This action is not available for this server.')
    mc.send_command(server_id, text)
    return {'ok':True}


def inventory(server_id, name):
    info = mc.get_server(server_id)
    if info['edition'] != 'java': raise ValueError('Inventory viewing is not available for Bedrock servers.')
    player = get_player(server_id, name)
    try: player_id = str(uuid.UUID(player['uuid']))
    except (KeyError, ValueError, TypeError): raise ValueError('Saved player data was not found.') from None
    folder = mc.server_dir(server_id).resolve()
    props = folder / 'server.properties'
    world = 'world'
    if props.exists():
        for line in props.read_text(encoding='utf-8').splitlines():
            if line.startswith('level-name='): world = line.split('=', 1)[1]; break
    path = (folder / world / 'playerdata' / (player_id + '.dat')).resolve()
    if not path.is_relative_to(folder) or not path.is_file(): raise ValueError('Saved player data was not found.')
    try:
        data = nbtlib.load(path, gzipped=True)
        items = []
        for entry in data.get('Inventory', []):
            items.append({'slot':int(entry.get('Slot', 0)), 'item':str(entry.get('id', '')), 'count':int(entry.get('count', entry.get('Count', 1)))})
    except (OSError, ValueError, KeyError, TypeError, EOFError): raise ValueError('Could not read saved inventory.') from None
    return {'items': items, 'saved': True}

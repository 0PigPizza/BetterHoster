"""Create vanilla server installations from official Minecraft downloads."""
import hashlib
import json
import re
import shutil
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent / 'servers'


def fetch_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'BetterHoster/1.0'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def download(url, target):
    request = urllib.request.Request(url, headers={'User-Agent': 'BetterHoster/1.0'})
    with urllib.request.urlopen(request, timeout=60) as response, target.open('wb') as out:
        shutil.copyfileobj(response, out)


def list_servers():
    if not ROOT.exists():
        return []
    result = []
    for path in ROOT.glob('*/dashboard.json'):
        if (path.parent / '.betterhoster-installing').exists():
            continue
        try:
            result.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue
    return result


def create_server(data):
    if data.get('eulaAccepted') is not True:
        raise ValueError('Accept the Minecraft EULA to create a server.')
    name, edition = data.get('name'), data.get('edition')
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or '\n' in name or '\r' in name:
        raise ValueError('Enter a server name.')
    if edition not in ('java', 'bedrock'):
        raise ValueError('Choose Java or Bedrock.')
    try:
        port, memory, players = (int(data[k]) for k in ('port', 'memory', 'players'))
    except (ValueError, TypeError, KeyError):
        raise ValueError('Enter valid server settings.') from None
    if not 1 <= port <= 65535 or not 1 <= memory <= 1024 or not 1 <= players <= 10000:
        raise ValueError('Enter valid server settings.')
    ROOT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.creating-', dir=ROOT) as temporary:
        directory = Path(temporary)
        if edition == 'java':
            manifest = fetch_json('https://piston-meta.mojang.com/mc/game/version_manifest_v2.json')
            version = manifest['latest']['release']
            release = next(v for v in manifest['versions'] if v['id'] == version)
            details = fetch_json(release['url'])
            server = details['downloads']['server']
            jar = directory / 'server.jar'
            download(server['url'], jar)
            with jar.open('rb') as f:
                if hashlib.file_digest(f, 'sha1').hexdigest() != server['sha1']:
                    raise ValueError('Server download failed verification.')
            properties = f'server-port={port}\nmax-players={players}\nwhite-list=false\n'
            (directory / 'server.properties').write_text(properties)
        else:
            links = fetch_json('https://net-secondary.web.minecraft-services.net/api/v1.0/download/links')['result']['links']
            url = next(item['downloadUrl'] for item in links if item['downloadType'] == 'serverBedrockLinux')
            if not re.fullmatch(r'https://www\.minecraft\.net/bedrockdedicatedserver/bin-linux/bedrock-server-[0-9.]+\.zip', url):
                raise ValueError('Invalid server download URL.')
            version = re.search(r'bedrock-server-([0-9.]+)\.zip', url).group(1)
            archive = directory / 'server.zip'
            download(url, archive)
            with zipfile.ZipFile(archive) as z:
                for entry in z.infolist():
                    if entry.filename.startswith('/') or '..' in Path(entry.filename).parts or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('Invalid server archive.')
                z.extractall(directory)
            archive.unlink()
            binary = directory / 'bedrock_server'
            binary.chmod(binary.stat().st_mode | 0o100)
            props = directory / 'server.properties'
            text = props.read_text()
            for key, value in {'server-name': name.strip(), 'server-port': port, 'max-players': players, 'allow-list': 'false'}.items():
                text = re.sub(r'^' + re.escape(key) + r'=.*$', lambda m: f'{key}={value}', text, flags=re.MULTILINE)
            props.write_text(text)
        (directory / 'eula.txt').write_text('eula=true\n')
        metadata = {'id': uuid.uuid4().hex, 'name': name.strip(), 'edition': edition, 'version': version, 'port': port, 'memory': memory, 'players': players}
        (directory / 'dashboard.json').write_text(json.dumps(metadata))
        directory.rename(ROOT / metadata['id'])
    return metadata

# Child processes and console output live for the lifetime of the dashboard server.
import collections
import os
import subprocess
import threading

PROCESSES = {}
OUTPUT = {}
PLAYER_SETS = {}
TPS_STATE = {}
LOCK = threading.RLock()


def server_dir(server_id):
    if not re.fullmatch(r'[0-9a-f]{32}', server_id):
        raise ValueError('Server not found.')
    path = ROOT / server_id
    if not (path / 'dashboard.json').is_file():
        raise ValueError('Server not found.')
    return path


def local_address():
    configured = os.environ.get('BETTERHOSTER_SERVER_ADDRESS')
    if configured:
        return configured
    if os.environ.get('BETTERHOSTER_IN_DOCKER') == '1':
        return ''
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('192.0.2.1', 9))
            return sock.getsockname()[0]
    except OSError:
        return '127.0.0.1'


def get_server(server_id):
    folder = server_dir(server_id)
    info = json.loads((folder / 'dashboard.json').read_text())
    info['running'] = is_running(server_id)
    info['address'] = local_address()
    info['eulaAccepted'] = (folder / 'eula.txt').is_file() and 'eula=true' in (folder / 'eula.txt').read_text().lower().splitlines()
    return info


def is_running(server_id):
    process = PROCESSES.get(server_id)
    return process is not None and process.poll() is None


def start_server(server_id):
    folder = server_dir(server_id)
    info = get_server(server_id)
    if not info['eulaAccepted']:
        raise ValueError('Please accept the Minecraft EULA before starting.')
    with LOCK:
        if is_running(server_id):
            raise ValueError('Server is already running.')
        if info['edition'] == 'java':
            executable = shutil.which('java')
            # Forge 1.20.1 requires a Java 17-compatible runtime.
            local_java = Path(__file__).parent / '.local-tools' / 'jdk17' / 'bin' / 'java'
            forge_args = info.get('launch_args', '')
            if '/minecraftforge/forge/1.20.1-' in forge_args and local_java.is_file():
                executable = str(local_java)
            if not executable:
                raise ValueError('Java is not installed.')
            if info.get('launch_args'):
                command = [executable, f"-Xmx{int(info['memory'])}G", '@' + info['launch_args'], 'nogui']
            else:
                command = [executable, f"-Xmx{int(info['memory'])}G", *info.get('launch_properties', []), '-jar', info.get('launch_jar', 'server.jar'), 'nogui']
        else:
            runtime = folder / info.get('run_directory', '')
            command = [str((runtime / 'bedrock_server').resolve())]
        env = os.environ.copy()
        if info['edition'] == 'bedrock':
            env['LD_LIBRARY_PATH'] = str(runtime.resolve())
        process = subprocess.Popen(command, cwd=runtime if info['edition'] == 'bedrock' else folder, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
        PROCESSES[server_id] = process
        OUTPUT[server_id] = collections.deque(maxlen=2000)
        PLAYER_SETS[server_id] = set()
        TPS_STATE.pop(server_id, None)
        def read_output():
            for line in process.stdout:
                line = line.rstrip('\n')
                OUTPUT[server_id].append(line)
                record_server_line(server_id, line)
            process.wait()
            OUTPUT[server_id].append(f'Server process exited ({process.returncode}).')
        threading.Thread(target=read_output, daemon=True).start()



def record_server_line(server_id, line):
    """Track join/leave messages and measured tick times from console output."""
    import time
    plain = re.sub(r'\x1b\[[0-9;]*m', '', line)
    joined = re.search(r'Player connected:\s*(.*?),\s*xuid:|(?:\]:\s*)([A-Za-z0-9_]{1,16}) joined the game', plain, re.I)
    departed = re.search(r'Player disconnected:\s*(.*?),\s*xuid:|(?:\]:\s*)([A-Za-z0-9_]{1,16}) left the game', plain, re.I)
    with LOCK:
        players = PLAYER_SETS.setdefault(server_id, set())
        if joined:
            players.add(next(x for x in joined.groups() if x is not None).strip())
        elif departed:
            players.discard(next(x for x in departed.groups() if x is not None).strip())
        target = re.search(r'Target tick rate:\s*([\d.]+) per second', plain, re.I)
        if target:
            TPS_STATE.setdefault(server_id, {})['target'] = float(target.group(1))
        timing = re.search(r'Average time per tick:\s*([\d.]+)ms', plain, re.I)
        paper = re.search(r'TPS from last .*?:\s*\*?([\d.]+)', plain, re.I)
        if timing:
            milliseconds = float(timing.group(1))
            state = TPS_STATE.setdefault(server_id, {})
            if milliseconds > 0:
                state['value'] = min(state.get('target', 20.0), 1000.0 / milliseconds)
                state['updated'] = time.monotonic()
        elif paper:
            state = TPS_STATE.setdefault(server_id, {})
            state['value'] = float(paper.group(1))
            state['updated'] = time.monotonic()

def send_command(server_id, command):
    with LOCK:
        if not is_running(server_id):
            raise ValueError('Server is not running.')
        if not isinstance(command, str) or not command.strip() or '\n' in command or '\r' in command or len(command) > 1024:
            raise ValueError('Enter a valid command.')
        process = PROCESSES[server_id]
        process.stdin.write(command + '\n')
        process.stdin.flush()


def stop_server(server_id):
    send_command(server_id, 'stop')


def browse_files(server_id, relative=''):
    base = server_dir(server_id).resolve()
    if not isinstance(relative, str) or '\x00' in relative:
        raise ValueError('Invalid path.')
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise ValueError('Invalid path.')
    if not target.exists():
        raise ValueError('File not found.')
    if target.is_dir():
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if not item.resolve().is_relative_to(base):
                continue
            entries.append({'name': item.name, 'path': item.relative_to(base).as_posix(), 'directory': item.is_dir()})
        return {'path': target.relative_to(base).as_posix().replace('.', '', 1) if target == base else target.relative_to(base).as_posix(), 'entries': entries}
    if target.stat().st_size > 1024 * 1024:
        raise ValueError('File is too large to preview.')
    try:
        return {'path': target.relative_to(base).as_posix(), 'content': target.read_text(encoding='utf-8')}
    except (UnicodeDecodeError, OSError):
        raise ValueError('This file cannot be previewed as text.') from None


def shutdown_servers():
    with LOCK:
        active = [process for process in PROCESSES.values() if process.poll() is None]
        for process in active:
            try:
                process.stdin.write('stop\n')
                process.stdin.flush()
            except (OSError, BrokenPipeError):
                pass
    for process in active:
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()



def monitor_server(server_id):
    """Sample resources for the managed server process and its children."""
    import time
    import psutil

    server_dir(server_id)
    with LOCK:
        process = PROCESSES.get(server_id)
        running = process is not None and process.poll() is None
        lines = list(OUTPUT.get(server_id, ()))
        memory = 0
        uptime = 0
        if running:
            try:
                root = psutil.Process(process.pid)
                processes = [root] + root.children(recursive=True)
                for child in processes:
                    try:
                        memory += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                uptime = max(0, int(time.time() - root.create_time()))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                running = False
        events = []
        for line in lines:
            if re.search(r'\b(error|fatal|severe)\b', line, re.IGNORECASE):
                events.append({'level': 'error', 'message': line})
            elif re.search(r'\b(warn|warning)\b', line, re.IGNORECASE):
                events.append({'level': 'warning', 'message': line})
        info = json.loads((server_dir(server_id) / 'dashboard.json').read_text())
        players = None
        if running:
            if info['edition'] == 'bedrock':
                players = len(PLAYER_SETS.get(server_id, ()))
            else:
                try:
                    from mcstatus import JavaServer
                    status = JavaServer('127.0.0.1', int(info['port']), timeout=0.5).status()
                    players = status.players.online
                except Exception:
                    players = len(PLAYER_SETS.get(server_id, ())) if server_id in PLAYER_SETS else None
            state = TPS_STATE.setdefault(server_id, {})
            software = info.get('software', 'vanilla')
            version = str(info.get('version', ''))
            supports_tick = software in ('paper', 'purpur', 'folia') or bool(re.match(r'^(?:2[0-9]\.|1\.(?:2[1-9]|20\.(?:[3-9]|[1-9][0-9]))(?:\.|$))', version))
            now = time.monotonic()
            if info['edition'] == 'java' and supports_tick and now - state.get('last_query', 0) >= 15:
                state['last_query'] = now
                try:
                    send_command(server_id, 'tps' if software in ('paper', 'purpur', 'folia') else 'tick query')
                except (ValueError, OSError):
                    pass
        state = TPS_STATE.get(server_id, {})
        tps = round(state['value'], 1) if running and 'value' in state and time.monotonic() - state.get('updated', 0) < 45 else None
        maximum = info.get('players')
        try:
            properties = (server_dir(server_id) / 'server.properties').read_text()
            match = re.search(r'^max-players\s*=\s*(\d+)', properties, re.MULTILINE)
            if match:
                maximum = int(match.group(1))
        except OSError:
            pass
        return {'running': running, 'memory': memory, 'uptime': uptime, 'events': events[-100:], 'players': players, 'max_players': maximum, 'tps': tps}

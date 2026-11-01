"""Run the open-source VS Code web workbench inside a per-server sandbox."""
import json
import os
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import minecraft_servers

BASE = Path(__file__).parent.resolve()
INSTALL = BASE / '.local-editor' / 'openvscode-server-v1.109.5-linux-x64'
PROFILES = BASE / '.local-editor' / 'profiles'
LOCK = threading.Lock()
EDITORS = {}


def editor_url(server_id):
    folder = minecraft_servers.server_dir(server_id).resolve()
    with LOCK:
        existing = EDITORS.get(server_id)
        if existing and existing['process'].poll() is None:
            return existing['url']
        if not (INSTALL / 'bin' / 'openvscode-server').is_file():
            raise ValueError('VS Code editor is not installed.')
        profile = PROFILES / server_id
        user = profile / 'User'
        user.mkdir(parents=True, exist_ok=True)
        settings = {
            'workbench.colorTheme': 'Default Dark Modern',
            'workbench.colorCustomizations': {
                'editor.background': '#111111', 'editor.foreground': '#EDEDED',
                'sideBar.background': '#161616', 'sideBar.foreground': '#EDEDED',
                'sideBar.border': '#303030', 'activityBar.background': '#161616',
                'activityBar.foreground': '#BEBEBE', 'activityBar.border': '#303030',
                'editorGroupHeader.tabsBackground': '#161616', 'tab.activeBackground': '#1B1B1B',
                'tab.inactiveBackground': '#161616', 'panel.background': '#1B1B1B',
                'statusBar.background': '#161616', 'statusBar.foreground': '#EDEDED',
                'input.background': '#202020', 'input.foreground': '#EDEDED',
                'input.border': '#303030', 'focusBorder': '#D5D5D5',
                'list.activeSelectionBackground': '#292929', 'list.activeSelectionForeground': '#EDEDED',
                'list.inactiveSelectionBackground': '#292929', 'list.hoverBackground': '#383838',
                'button.background': '#EDEDED', 'button.foreground': '#111111',
                'button.hoverBackground': '#D5D5D5', 'button.secondaryBackground': '#242424',
                'button.secondaryForeground': '#EDEDED', 'button.secondaryHoverBackground': '#383838',
                'dropdown.background': '#202020', 'dropdown.foreground': '#EDEDED',
                'dropdown.border': '#303030', 'menu.background': '#1B1B1B',
                'menu.foreground': '#EDEDED', 'menu.selectionBackground': '#383838',
                'editor.selectionBackground': '#383838', 'editor.inactiveSelectionBackground': '#292929',
                'editorLineNumber.foreground': '#666666', 'editorLineNumber.activeForeground': '#EDEDED',
                'editorCursor.foreground': '#EDEDED', 'textLink.foreground': '#D5D5D5',
                'textLink.activeForeground': '#EDEDED', 'widget.border': '#303030',
                'titleBar.activeBackground': '#161616', 'titleBar.activeForeground': '#EDEDED',
                'statusBar.noFolderBackground': '#161616', 'panel.border': '#303030',
                'tab.activeBorderTop': '#D5D5D5', 'progressBar.background': '#D5D5D5',
            },
            'security.workspace.trust.enabled': False,
            'telemetry.telemetryLevel': 'off',
        }
        (user / 'settings.json').write_text(json.dumps(settings))
        # Web clients keep their own user settings, so set the workbench defaults
        # in the bundled theme extension as well as the remote profile.
        theme_package = INSTALL / 'extensions' / 'theme-defaults' / 'package.json'
        package = json.loads(theme_package.read_text())
        package['contributes']['configurationDefaults'] = {
            'workbench.colorTheme': settings['workbench.colorTheme'],
            'workbench.colorCustomizations': settings['workbench.colorCustomizations'],
        }
        theme_package.write_text(json.dumps(package, separators=(',', ':')))
        token = secrets.token_urlsafe(32)
        token_file = profile / 'token'
        token_file.write_text(token)
        token_file.chmod(0o600)
        first = int(os.environ.get('BETTERHOSTER_EDITOR_PORT_MIN', '0'))
        last = int(os.environ.get('BETTERHOSTER_EDITOR_PORT_MAX', str(first)))
        if first and not 1024 <= first <= last <= 65535:
            raise ValueError('Invalid editor port range.')
        port = None
        for candidate in range(first, last + 1):
            try:
                with socket.socket() as listener:
                    listener.bind(('0.0.0.0' if first else '127.0.0.1', candidate))
                    port = listener.getsockname()[1]
                    break
            except OSError:
                continue
        if port is None:
            raise ValueError('No editor port is available.')
        bind_host = '0.0.0.0' if first else '127.0.0.1'
        command = ['bwrap', '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--ro-bind', '/usr', '/usr',
            '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--ro-bind', '/etc', '/etc', *(['--dir', '/proc'] if os.environ.get('BETTERHOSTER_IN_DOCKER') == '1' else ['--proc', '/proc']), '--dev', '/dev', '--tmpfs', '/tmp',
            '--dir', '/home', '--dir', '/home/editor', '--bind', str(profile), '/home/editor',
            '--ro-bind', str(INSTALL), '/editor', '--bind', str(folder), '/workspace',
            '--setenv', 'HOME', '/home/editor', '--setenv', 'PATH', '/usr/bin',
            '--chdir', '/workspace', '/editor/bin/openvscode-server', '--host', bind_host,
            '--port', str(port), '--connection-token-file', '/home/editor/token',
            '--server-data-dir', '/home/editor/server', '--user-data-dir', '/home/editor',
            '--telemetry-level', 'off']
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True, env={'PATH': '/usr/bin', 'HOME': str(profile)})
        for _ in range(100):
            if process.poll() is not None:
                raise ValueError('Unable to start VS Code.')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            process.terminate()
            raise ValueError('VS Code took too long to start.')
        url = f'http://127.0.0.1:{port}/?' + urlencode({'tkn': token, 'folder': '/workspace'})
        EDITORS[server_id] = {'process': process, 'url': url}
        return url


def shutdown():
    for editor in EDITORS.values():
        if editor['process'].poll() is None:
            editor['process'].terminate()

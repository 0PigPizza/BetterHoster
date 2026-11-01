"""Local admin registration server."""
import json
import os
import secrets
import signal
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pymysql
import minecraft_servers
import server_versions
import server_install_choice
import server_modpacks
import server_addons
import server_config
import server_players
import server_curseforge
import server_hangar
import server_addon_details
import vscode_editor
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

BASE = Path(__file__).parent
SESSIONS = set()
# Local settings are generated during setup; explicit environment values take precedence.
env_file = Path(__file__).with_name(".env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value)


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, payload, cookie=None):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def authenticated(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            session = cookie.get("session")
            return session is not None and session.value in SESSIONS
        except Exception:
            return False

    def do_GET(self):
        parsed = urlsplit(self.path)
        parts = parsed.path.strip('/').split('/')
        if len(parts) == 3 and parts[:2] == ['api', 'servers']:
            if not self.authenticated():
                self.respond(401, {"message": "Please log in."})
                return
            try:
                server_id = parts[2]
                info = minecraft_servers.get_server(server_id)
                query = parse_qs(parsed.query, keep_blank_values=True)
                if 'addon-details' in query:
                    result = server_addon_details.details(query.get('source', [''])[0], query['addon-details'][0], query.get('kind', ['mods'])[0])
                elif 'hangar-versions' in query:
                    result = {'versions': server_hangar.available_versions(server_id, query['hangar-versions'][0])}
                elif 'hangar' in query:
                    result = server_hangar.search(server_id, query['hangar'][0], int(query.get('offset', ['0'])[0]))
                elif 'curseforge-versions' in query:
                    result = {'versions': server_curseforge.available_versions(server_id, query['curseforge-versions'][0])}
                elif 'curseforge' in query:
                    result = server_curseforge.search(server_id, query['curseforge'][0], int(query.get('page', ['1'])[0]))
                elif 'inventory' in query:
                    result = server_players.inventory(server_id, query['inventory'][0])
                elif 'players' in query:
                    result = server_players.players(server_id)
                elif 'config' in query:
                    result = server_config.get(server_id)
                elif 'mod-versions' in query:
                    result = {'versions': server_addons.mod_versions(server_id, query['mod-versions'][0], query.get('kind', ['mods'])[0])}
                elif 'addons' in query:
                    result = server_addons.search(server_id, query['addons'][0], query.get('q', [''])[0], int(query.get('offset', ['0'])[0]))
                elif 'modpacks' in query:
                    result = server_modpacks.search(query['modpacks'][0], int(query.get('offset', ['0'])[0]))
                elif 'modpack-versions' in query:
                    result = {'versions': server_modpacks.versions(query['modpack-versions'][0])}
                elif 'versions' in query:
                    result = {'versions': server_versions.catalog(query['versions'][0])}
                elif 'monitor' in query:
                    result = minecraft_servers.monitor_server(server_id)
                elif 'files' in query:
                    result = minecraft_servers.browse_files(server_id, query['files'][0])
                elif 'console' in query:
                    result = {'lines': list(minecraft_servers.OUTPUT.get(server_id, [])), 'running': info['running']}
                else:
                    info['addons'] = server_addons.compatibility(info)
                    result = info
                self.respond(200, result)
            except ValueError as error:
                self.respond(400, {"message": str(error) or "Unable to load the requested information."})
            except (OSError, KeyError, TypeError, StopIteration):
                self.respond(503, {"message": "Unable to load the requested information."})
            return
        if self.path == "/api/servers":
            if not self.authenticated():
                self.respond(401, {"message": "Please log in."})
                return
            self.respond(200, {"servers": minecraft_servers.list_servers()})
            return
        if self.path == "/":
            try:
                connection = pymysql.connect(host=os.environ.get("MYSQL_HOST", "localhost"),
                    port=int(os.environ.get("MYSQL_PORT", "3306")), user=os.environ["MYSQL_USER"],
                    password=os.environ["MYSQL_PASSWORD"], database=os.environ["MYSQL_DATABASE"],
                    charset="utf8mb4", connect_timeout=5)
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1 FROM admins LIMIT 1")
                        admin_exists = cursor.fetchone() is not None
                finally:
                    connection.close()
            except (pymysql.MySQLError, KeyError, ValueError):
                self.send_error(503, "Database unavailable")
                return
            if not admin_exists:
                page = BASE / "admin-register.html"
            else:
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                    session = cookie.get("session")
                except Exception:
                    session = None
                if session is None or session.value not in SESSIONS:
                    self.send_response(303)
                    self.send_header("Location", "/login")
                    self.end_headers()
                    return
                page = BASE / "main.html"
        elif self.path == "/server/create":
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                session = cookie.get("session")
            except Exception:
                session = None
            if session is None or session.value not in SESSIONS:
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            page = BASE / "index.html"
        elif len(parts) == 2 and parts[0] == 'server' and parts[1] != 'create':
            if not self.authenticated():
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            try:
                minecraft_servers.server_dir(parts[1])
            except ValueError:
                self.send_error(404)
                return
            page = BASE / "server-dashboard.html"
        elif self.path == "/login":
            page = BASE / "admin-login.html"
        else:
            self.send_error(404)
            return
        data = page.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parts = urlsplit(self.path).path.strip('/').split('/')
        if len(parts) == 4 and parts[:2] == ['api', 'servers']:
            if not self.authenticated():
                self.respond(401, {"message": "Please log in."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 4096:
                    raise ValueError('Invalid request.')
                body = json.loads(self.rfile.read(length) or b'{}')
                server_id, action = parts[2], parts[3]
                folder = minecraft_servers.server_dir(server_id)
                if action == 'install-hangar':
                    self.respond(200, server_hangar.install(server_id, body.get('project'), body.get('version'), body.get('allow_incompatible', False)))
                    return
                if action == 'remove-hangar':
                    self.respond(200, server_hangar.remove(server_id, body.get('project')))
                    return
                if action == 'install-curseforge':
                    self.respond(200, server_curseforge.install(server_id, body.get('project'), body.get('version'), body.get('allow_incompatible', False)))
                    return
                if action == 'remove-curseforge':
                    self.respond(200, server_curseforge.remove(server_id, body.get('project')))
                    return
                if action == 'player-action':
                    self.respond(200, server_players.command(server_id, body.get('player'), body.get('action'), body.get('value')))
                    return
                if action == 'save-config':
                    self.respond(200, server_config.save(server_id, body.get('settings')))
                    return
                if action == 'remove-mod':
                    self.respond(200, server_addons.remove_mod(server_id, body.get('project'), body.get('kind', 'mods')))
                    return
                if action == 'install-addon':
                    self.respond(200, server_addons.install(server_id, body.get('kind'), body.get('project'), body.get('version'), body.get('allow_incompatible', False)))
                    return
                if action == 'install-modpack':
                    result = server_install_choice.install(server_id, body.get('files'), 'modpack', body.get('project'), body.get('version'))
                    self.respond(200, {'server': result})
                    return
                if action == 'install-version':
                    result = server_install_choice.install(server_id, body.get('files'), 'version', body.get('software'), body.get('version'))
                    self.respond(200, {'server': result})
                    return
                if action == 'editor':
                    self.respond(200, {'url': vscode_editor.editor_url(server_id)})
                    return
                if action == 'start':
                    minecraft_servers.start_server(server_id)
                elif action == 'stop':
                    minecraft_servers.stop_server(server_id)
                elif action == 'command':
                    minecraft_servers.send_command(server_id, body.get('command'))
                elif action == 'accept-eula' and body.get('eulaAccepted') is True:
                    (folder / 'eula.txt').write_text('eula=true\n')
                else:
                    raise ValueError('Invalid request.')
                self.respond(200, {"ok": True})
            except (ValueError, TypeError, KeyError, OSError) as error:
                self.respond(400, {"message": str(error) or 'Unable to complete the request.'})
            except Exception:
                self.respond(500, {"message": 'Unable to complete the request.'})
            return
        if self.path == "/api/servers":
            if not self.authenticated():
                self.respond(401, {"message": "Please log in."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid server settings.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Invalid server settings.")
                server = minecraft_servers.create_server(body)
            except (ValueError, TypeError, KeyError) as error:
                self.respond(400, {"message": str(error) or "Invalid server settings."})
                return
            except Exception:
                self.respond(500, {"message": "Unable to download or create the server."})
                return
            self.respond(201, {"server": server})
            return
        if self.path not in ("/api/admin/register", "/api/admin/login"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError()
            body = json.loads(self.rfile.read(length))
            username, password = (body.get(k) for k in ("username", "password"))
            if not all(isinstance(v, str) for v in (username, password)):
                raise ValueError()
            if self.path == "/api/admin/register" and (not 3 <= len(username) <= 254 or "@" not in username or not 1 <= len(password) <= 128 or body.get("confirmPassword") != password):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
            self.respond(400, {"message": "Enter an email and matching passwords."})
            return
        try:
            connection = pymysql.connect(host=os.environ.get("MYSQL_HOST", "localhost"),
                port=int(os.environ.get("MYSQL_PORT", "3306")), user=os.environ["MYSQL_USER"],
                password=os.environ["MYSQL_PASSWORD"], database=os.environ["MYSQL_DATABASE"],
                charset="utf8mb4", connect_timeout=5)
            try:
                if self.path == "/api/admin/login":
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT password_hash FROM admins WHERE username = %s", (username,))
                        row = cursor.fetchone()
                    try:
                        valid = row is not None and PasswordHasher().verify(row[0], password)
                    except (VerifyMismatchError, VerificationError, InvalidHashError):
                        valid = False
                    if not valid:
                        self.respond(401, {"message": "Invalid email or password."})
                        return
                    session = secrets.token_urlsafe(32)
                    SESSIONS.add(session)
                    self.respond(200, {"message": "Logged in."},
                                 f"session={session}; HttpOnly; SameSite=Strict; Path=/")
                    return
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO admins (id, username, password_hash) VALUES (1, %s, %s)",
                                   (username, PasswordHasher().hash(password)))
                connection.commit()
            finally:
                connection.close()
        except pymysql.err.IntegrityError:
            self.respond(409, {"message": "An admin has already been registered."})
            return
        except (pymysql.MySQLError, KeyError, ValueError):
            self.respond(500, {"message": "Unable to register admin. Check server configuration."})
            return
        self.respond(201, {"message": "Admin registered successfully."})


if __name__ == "__main__":
    def shutdown(signum, frame):
        vscode_editor.shutdown()
        minecraft_servers.shutdown_servers()
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    for server_id in os.environ.pop('BETTERHOSTER_RESUME_SERVERS', os.environ.pop('YAMCH_RESUME_SERVERS', '')).split(','):
        if server_id:
            try:
                minecraft_servers.start_server(server_id)
            except (ValueError, OSError) as error:
                print(f'Unable to resume server {server_id}: {error}', file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()

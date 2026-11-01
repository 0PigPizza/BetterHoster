"""Exercise registration, login, and the isolated editor in a Compose test stack."""
import http.cookiejar
import json
import subprocess
import time
import urllib.request

base = 'http://127.0.0.1:18080'
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
for _ in range(90):
    try:
        if 'Register admin' in opener.open(base + '/', timeout=2).read().decode(): break
    except Exception:
        time.sleep(1)
else: raise RuntimeError('Dashboard did not become ready.')


def post(path, data):
    request = urllib.request.Request(base + path, data=json.dumps(data).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    return opener.open(request, timeout=30)


credentials = {'username': 'test@example.com', 'password': 'TestPassword123'}
assert post('/api/admin/register', dict(credentials, confirmPassword=credentials['password'])).status == 201
assert post('/api/admin/login', credentials).status == 200
assert opener.open(base + '/').status == 200
fixture = ("import json,pathlib; p=pathlib.Path('/app/servers/'+'a'*32); p.mkdir(); "
           "(p/'dashboard.json').write_text(json.dumps({'id':'a'*32,'name':'Test',"
           "'edition':'java','version':'1.20.1','port':25565,'players':20,'memory':1})); "
           "(p/'server.properties').write_text('server-port=25565\\n'); "
           "(p/'eula.txt').write_text('eula=true\\n')")
subprocess.run(['docker', 'compose', 'exec', '-T', '--user', 'betterhoster', 'dashboard', 'python', '-c', fixture], check=True)
editor = json.load(post('/api/servers/' + 'a' * 32 + '/editor', {}))
assert opener.open(editor['url'], timeout=15).status == 200
print('Registration, login, dashboard, and editor smoke test passed.')

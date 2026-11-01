"""Catalog and install official Minecraft Java server software."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import minecraft_servers as mc

SOFTWARE = ('vanilla', 'snapshot', 'paper', 'purpur', 'folia', 'fabric', 'quilt', 'forge', 'neoforge', 'arclight', 'bedrock', 'bedrock-preview', 'spigot')


def version_type(version):
    lowered = version.lower()
    if 'beta' in lowered:
        return 'beta'
    if 'snapshot' in lowered or re.match(r'\d\dw\d\d', lowered):
        return 'snapshot'
    if 'pre' in lowered or 'rc' in lowered or 'experimental' in lowered:
        return 'prerelease'
    if re.fullmatch(r'\d+\.\d+\.\d+', lowered):
        return 'patch'
    return 'stable'


def catalog(software):
    if software not in SOFTWARE:
        raise ValueError('Unknown server software.')
    if software in ('bedrock', 'bedrock-preview'):
        links = mc.fetch_json('https://net-secondary.web.minecraft-services.net/api/v1.0/download/links')['result']['links']
        kind = 'serverBedrockLinux' if software == 'bedrock' else 'serverBedrockPreviewLinux'
        url = next(item['downloadUrl'] for item in links if item['downloadType'] == kind)
        version = re.search(r'bedrock-server-([0-9.]+)\.zip$', url)
        if not version:
            raise ValueError('No Bedrock version is available.')
        return [{'id': version.group(1), 'type': 'stable' if software == 'bedrock' else 'prerelease'}]
    if software in ('vanilla', 'snapshot'):
        entries = mc.fetch_json('https://piston-meta.mojang.com/mc/game/version_manifest_v2.json')['versions']
        versions = [v['id'] for v in entries if v['type'] == 'snapshot' or (software == 'vanilla' and v['type'] == 'release')]
    elif software in ('paper', 'folia'):
        data = mc.fetch_json(f'https://fill.papermc.io/v3/projects/{software}')
        versions = list(dict.fromkeys(v for group in data['versions'].values() for v in group))
    elif software == 'purpur':
        versions = list(reversed(mc.fetch_json('https://api.purpurmc.org/v2/purpur')['versions']))
    elif software in ('fabric', 'quilt'):
        url = 'https://meta.fabricmc.net/v2/versions/game' if software == 'fabric' else 'https://meta.quiltmc.org/v3/versions/game'
        versions = [v['version'] for v in mc.fetch_json(url)]
    elif software == 'forge':
        request = urllib.request.Request('https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml', headers={'User-Agent': 'BetterHoster/1.0'})
        with urllib.request.urlopen(request, timeout=30) as response:
            xml = ET.fromstring(response.read())
        versions = [v.text for v in xml.findall('./versioning/versions/version') if v.text and re.match(r'^(?:1\.(?:1[7-9]|2[0-9])|2[0-9]\.)', v.text)]
    elif software == 'spigot':
        request = urllib.request.Request('https://hub.spigotmc.org/versions/', headers={'User-Agent': 'BetterHoster/1.0'})
        with urllib.request.urlopen(request, timeout=30) as response:
            index = response.read().decode('utf-8')
        versions = list(reversed(re.findall(r'href="([0-9]+\.[0-9]+(?:\.[0-9]+)?)\.json"', index)))
    elif software == 'arclight':
        releases = mc.fetch_json('https://api.github.com/repos/IzzelAliz/Arclight/releases?per_page=100')
        versions = [a['name'][:-4] for release in releases for a in release.get('assets', []) if a['name'].endswith('.jar') and a['name'].startswith('arclight-')]
    else:
        versions = list(reversed(mc.fetch_json('https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge')['versions']))
    return [{'id': v, 'type': version_type(v)} for v in versions]


def verify(path, algorithm, expected):
    with path.open('rb') as f:
        if hashlib.file_digest(f, algorithm).hexdigest() != expected:
            raise ValueError('Download verification failed.')


def install(server_id, software, version, loader_version=None):
    folder = mc.server_dir(server_id)
    if software not in SOFTWARE or not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', version):
        raise ValueError('Select a valid software and version.')
    # The lock also prevents the server starting while files are being installed.
    with mc.LOCK:
        info = mc.get_server(server_id)
        if (info['edition'] == 'bedrock') != (software in ('bedrock', 'bedrock-preview')):
            raise ValueError('Choose software for this server edition.')
        if info['running']:
            raise ValueError('Stop the server before installing a version.')
        current_software = info.get('software', 'bedrock' if info['edition'] == 'bedrock' else 'vanilla')
        if current_software == 'snapshot':
            current_software = 'vanilla'
        if not loader_version and current_software == software and info['version'] == version:
            raise ValueError('This version is already installed.')
        if not any(item['id'] == version for item in catalog(software)):
            raise ValueError('This version is no longer available.')
        base = folder / '.yamch' / 'versions'
        base.mkdir(parents=True, exist_ok=True)
        install_id = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='.install-', dir=base) as temporary:
            directory = Path(temporary)
            jar = directory / 'server.jar'
            relative = f'.yamch/versions/{install_id}'
            if software in ('bedrock', 'bedrock-preview'):
                kind = 'serverBedrockLinux' if software == 'bedrock' else 'serverBedrockPreviewLinux'
                links = mc.fetch_json('https://net-secondary.web.minecraft-services.net/api/v1.0/download/links')['result']['links']
                url = next(item['downloadUrl'] for item in links if item['downloadType'] == kind)
                allowed = r'https://www\.minecraft\.net/bedrockdedicatedserver/bin-linux(?:-preview)?/bedrock-server-[0-9.]+\.zip'
                if not re.fullmatch(allowed, url) or not url.endswith('bedrock-server-' + version + '.zip'):
                    raise ValueError('This Bedrock version is no longer available.')
                archive = directory / 'server.zip'
                mc.download(url, archive)
                import zipfile
                with zipfile.ZipFile(archive) as z:
                    for entry in z.infolist():
                        if entry.filename.startswith('/') or '..' in Path(entry.filename).parts or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                            raise ValueError('Invalid server archive.')
                    z.extractall(directory)
                archive.unlink()
                executable = directory / 'bedrock_server'
                executable.chmod(executable.stat().st_mode | 0o100)
                for name in ('worlds', 'config', 'premium_cache', 'development_behavior_packs', 'development_resource_packs', 'development_skin_packs'):
                    shared = folder / name
                    installed = directory / name
                    if not shared.exists():
                        if installed.is_dir(): shutil.copytree(installed, shared)
                        else: shared.mkdir(parents=True)
                    if installed.is_dir(): shutil.rmtree(installed)
                    installed.symlink_to(shared.resolve(), target_is_directory=True)
                for name in ('server.properties', 'allowlist.json', 'permissions.json'):
                    shared = folder / name
                    installed = directory / name
                    if not shared.exists() and installed.is_file(): shutil.copy2(installed, shared)
                    if installed.exists(): installed.unlink()
                    installed.symlink_to(shared.resolve())
                info['run_directory'] = relative
            elif software in ('vanilla', 'snapshot'):
                manifest = mc.fetch_json('https://piston-meta.mojang.com/mc/game/version_manifest_v2.json')
                details = mc.fetch_json(next(item['url'] for item in manifest['versions'] if item['id'] == version))
                download = details['downloads']['server']
                mc.download(download['url'], jar)
                verify(jar, 'sha1', download['sha1'])
            elif software in ('paper', 'folia'):
                builds = mc.fetch_json(f'https://fill.papermc.io/v3/projects/{software}/versions/{quote(version)}/builds')
                stable = [b for b in builds if b.get('channel') == 'STABLE' and 'server:default' in b.get('downloads', {})]
                available = stable or [b for b in builds if 'server:default' in b.get('downloads', {})]
                if not available:
                    raise ValueError('No server build is available for this version.')
                download = available[0]['downloads']['server:default']
                mc.download(download['url'], jar)
                verify(jar, 'sha256', download['checksums']['sha256'])
            elif software == 'spigot':
                java = shutil.which('java')
                if not java or not shutil.which('javac') or not shutil.which('git'):
                    raise ValueError('Spigot requires a Java development kit and Git.')
                build = directory / 'build'
                build.mkdir()
                tool = build / 'BuildTools.jar'
                mc.download('https://hub.spigotmc.org/jenkins/job/BuildTools/lastSuccessfulBuild/artifact/target/BuildTools.jar', tool)
                try:
                    result = subprocess.run([java, '-jar', str(tool.resolve()), '--rev', version, '--compile', 'SPIGOT', '--nogui'],
                                            cwd=build, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)
                except subprocess.TimeoutExpired:
                    raise ValueError('Spigot build timed out.') from None
                artifacts = list(build.glob('spigot-*.jar'))
                if result.returncode or len(artifacts) != 1:
                    raise ValueError('Spigot build failed. This version may require a different Java development kit.')
                shutil.move(str(artifacts[0]), str(jar))
                shutil.rmtree(build)
            elif software == 'arclight':
                releases = mc.fetch_json('https://api.github.com/repos/IzzelAliz/Arclight/releases?per_page=100')
                asset = next((a for release in releases for a in release.get('assets', []) if a['name'] == version + '.jar'), None)
                if asset is None or not asset['browser_download_url'].startswith('https://github.com/IzzelAliz/Arclight/releases/download/'):
                    raise ValueError('This Arclight version is no longer available.')
                mc.download(asset['browser_download_url'], jar)
            elif software == 'purpur':
                mc.download(f'https://api.purpurmc.org/v2/purpur/{quote(version)}/latest/download', jar)
            elif software == 'fabric':
                loaders = mc.fetch_json(f'https://meta.fabricmc.net/v2/versions/loader/{quote(version)}')
                loader = next((x['loader']['version'] for x in loaders if x['loader']['version'] == loader_version), None) if loader_version else next((x['loader']['version'] for x in loaders if x['loader']['stable']), None)
                installers = mc.fetch_json('https://meta.fabricmc.net/v2/versions/installer')
                installer = next((x['version'] for x in installers if x['stable']), None)
                if not loader or not installer:
                    raise ValueError('No compatible Fabric installer is available.')
                mc.download(f'https://meta.fabricmc.net/v2/versions/loader/{quote(version)}/{quote(loader)}/{quote(installer)}/server/jar', jar)
            elif software == 'quilt':
                java = shutil.which('java')
                if not java:
                    raise ValueError('Java is not installed.')
                installer_data = mc.fetch_json('https://meta.quiltmc.org/v3/versions/installer')[0]
                installer = directory / 'installer.jar'
                mc.download(installer_data['url'], installer)
                with urllib.request.urlopen(installer_data['url'] + '.sha256', timeout=30) as response:
                    installer_sha256 = response.read(128).decode('ascii').strip().split()[0]
                verify(installer, 'sha256', installer_sha256)
                loader_args = [loader_version] if loader_version else []
                if loader_version and not re.fullmatch(r'[A-Za-z0-9._+-]{1,100}', loader_version):
                    raise ValueError('Invalid Quilt loader version.')
                result = subprocess.run([java, '-jar', str(installer.resolve()), 'install', 'server', version, *loader_args,
                                         '--install-dir=' + str(directory.resolve()), '--download-server'],
                                        cwd=directory, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
                if result.returncode or not (directory / 'quilt-server-launch.jar').is_file():
                    raise ValueError('Quilt installation failed. Check the server version and Java runtime.')
                installer.unlink()
                info['launch_jar'] = relative + '/quilt-server-launch.jar'
                info['launch_properties'] = ['-Dloader.gameJarPath=' + relative + '/server.jar']
            else:
                java = shutil.which('java')
                if not java:
                    raise ValueError('Java is not installed.')
                if software == 'forge':
                    url = f'https://maven.minecraftforge.net/net/minecraftforge/forge/{quote(version)}/forge-{quote(version)}-installer.jar'
                else:
                    url = f'https://maven.neoforged.net/releases/net/neoforged/neoforge/{quote(version)}/neoforge-{quote(version)}-installer.jar'
                installer = directory / 'installer.jar'
                mc.download(url, installer)
                result = subprocess.run([java, '-jar', str(installer.resolve()), '--installServer'], cwd=directory,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
                if result.returncode:
                    raise ValueError(f'{software.capitalize()} installation failed. Check the server version and Java runtime.')
                installer.unlink()
                args_files = list(directory.glob('libraries/net/minecraftforge/forge/*/unix_args.txt' if software == 'forge' else 'libraries/net/neoforged/neoforge/*/unix_args.txt'))
                if len(args_files) != 1:
                    raise ValueError('Installation did not produce startup arguments.')
                args = args_files[0]
                content = args.read_text()
                shim = re.search(r'-jar\s+(forge-[A-Za-z0-9._-]+-shim\.jar)', content)
                if software == 'forge' and shim:
                    # Newer Forge shims resolve libraries relative to the server root.
                    for source in (directory / 'libraries').rglob('*'):
                        if not source.is_file():
                            continue
                        target = folder / 'libraries' / source.relative_to(directory / 'libraries')
                        if target.exists():
                            if not target.is_file() or source.stat().st_size != target.stat().st_size:
                                raise ValueError('An existing Forge library conflicts with the installation.')
                            with source.open('rb') as a, target.open('rb') as b:
                                if hashlib.file_digest(a, 'sha256').digest() != hashlib.file_digest(b, 'sha256').digest():
                                    raise ValueError('An existing Forge library conflicts with the installation.')
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, target)
                    content = content.replace(shim.group(1), relative + '/' + shim.group(1))
                else:
                    content = content.replace('libraries/', relative + '/libraries/').replace('-DlibraryDirectory=libraries', f'-DlibraryDirectory={relative}/libraries')
                args.write_text(content)
                info['launch_args'] = relative + '/' + args.relative_to(directory).as_posix()
            if software in ('bedrock', 'bedrock-preview'):
                info.pop('launch_jar', None)
                info.pop('launch_args', None)
                info.pop('launch_properties', None)
            elif software in ('neoforge', 'forge'):
                info.pop('launch_jar', None)
                info.pop('launch_properties', None)
            else:
                info.pop('launch_args', None)
                if software != 'quilt':
                    info['launch_jar'] = relative + '/server.jar'
                    info.pop('launch_properties', None)
            # Never replace the live world, configuration, or original installation.
            directory.rename(base / install_id)
            info.pop('running', None)
            info.pop('address', None)
            info.pop('eulaAccepted', None)
            for key in ('version_label', 'modpack_project', 'modpack_name', 'modpack_loader', 'modpack_game_version'):
                info.pop(key, None)
            info['software'], info['version'] = software, version
            metadata = folder / 'dashboard.json'
            temp_metadata = folder / ('.dashboard-' + install_id + '.json')
            temp_metadata.write_text(json.dumps(info))
            os.replace(temp_metadata, metadata)
        return info

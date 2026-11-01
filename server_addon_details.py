"""Project details for the mod and plugin catalogs."""
import re
import urllib.parse
import minecraft_servers as mc
import server_curseforge
import server_hangar


def details(source, project, kind="mods"):
    if source == 'modrinth':
        if not isinstance(project,str) or not re.fullmatch(r'[A-Za-z0-9]{8}', project): raise ValueError('Select a project.')
        data = mc.fetch_json('https://api.modrinth.com/v2/project/' + project)
        return {'name':data.get('title',''), 'description':data.get('description',''), 'body':str(data.get('body') or '')[:40000], 'downloads':data.get('downloads'), 'categories':data.get('categories',[]), 'url':'https://modrinth.com/' + ('plugin' if kind == 'plugins' else 'mod') + '/' + data.get('slug',project)}
    if source == 'curseforge':
        project = server_curseforge.project_id(project); data = mc.fetch_json('https://api.modpacks.ch/public/mod/' + project)
        if data.get('status') == 'error': raise ValueError('Unable to load project details.')
        url = next((link.get('link','') for link in data.get('links',[]) if isinstance(link,dict) and urllib.parse.urlsplit(link.get('link','')).hostname == 'www.curseforge.com' and link.get('link','').startswith('https://')), 'https://www.curseforge.com/minecraft/mc-mods')
        return {'name':data.get('name',''), 'description':data.get('synopsis',''), 'body':str(data.get('description') or '')[:40000], 'downloads':data.get('installs'), 'categories':[t.get('name','') for t in (data.get('tags') or []) if isinstance(t,dict)], 'url':url}
    if source == 'hangar':
        data = server_hangar.get_project(project); namespace = data.get('namespace') or {}
        return {'name':data.get('name',''), 'description':data.get('description',''), 'body':str(data.get('mainPageContent') or '')[:40000], 'downloads':(data.get('stats') or {}).get('downloads'), 'categories':[data.get('category','')], 'url':'https://hangar.papermc.io/' + urllib.parse.quote(namespace.get('owner',''),safe='') + '/' + urllib.parse.quote(namespace.get('slug',project),safe='')}
    raise ValueError('Invalid project source.')

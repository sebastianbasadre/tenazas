import hashlib
import json
import os
import shlex
import subprocess
import tarfile
from pathlib import Path

LOCAL = Path(os.environ.get('TENAZAS_REVIEW_HOME', str(Path.home() / 'codex-reviews')))
REPO = Path(__file__).resolve().parents[1]
BASE = os.environ.get('TENAZAS_WRITER_STAGING', '/volume1/docker/tenazas-mcp-review-d5d1a45/private-writer-replay')
PROD = '/volume1/docker/openclaw/workspace/runtime'
assert BASE.startswith('/volume1/docker/tenazas-mcp-review-d5d1a45/') and '..' not in BASE.split('/')
NAME = 'tenazas-private-writer-staging'
IMAGE = 'tenazas-mcp-review:d5d1a45'
DOCKER = '/usr/local/bin/docker'
SSH = ['ssh', '-F', 'NUL', '-p', '4822', '-i', str(Path(os.environ['USERPROFILE']) / '.ssh/id_claude_nas'),
       '-o', 'BatchMode=yes', 'sbasadre@192.168.2.2']
SCP = ['scp', '-O', '-F', 'NUL', '-P', '4822', '-i', str(Path(os.environ['USERPROFILE']) / '.ssh/id_claude_nas')]


def remote(args, payload=None, check=True):
    command = args if isinstance(args, str) else shlex.join(args)
    result = subprocess.run(SSH + [command], input=payload, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stdout.decode()[-2000:] + result.stderr.decode()[-2000:])
    return result


def acls(paths):
    assert all(path.startswith(BASE + '/') for path in paths)
    # Host UID 1034 is correctly unable to traverse private UID 1026 directories.
    # Run the NAS's native ACL ioctl utility as that owner, in a read-only,
    # networkless inspector with only staging and system libraries mounted.
    script = '''import json,os,subprocess,sys
env=dict(os.environ,LD_LIBRARY_PATH="/usr/syno/lib:/nas/usr/lib")
result=[]
for p in json.loads(sys.argv[1]):
 r=subprocess.run(["/nas-synoacltool","-get",p],capture_output=True,env=env)
 value=r.stdout.decode()+r.stderr.decode()
 assert "It's Linux mode" in value,(p,value)
 result.append({"path":p,"result":value.strip()})
print(json.dumps(result))'''
    result = remote([DOCKER, 'run', '--rm', '--network', 'none', '--read-only', '--user', '1026:1026',
      '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
      '-v', '/usr/syno/bin/synoacltool:/nas-synoacltool:ro', '-v', '/usr/syno/lib:/usr/syno/lib:ro',
      '-v', '/usr/lib:/nas/usr/lib:ro', '-v', BASE + ':/staging:ro',
      '--entrypoint', 'python3', IMAGE, '-c', script,
      json.dumps(['/staging' + path.removeprefix(BASE) for path in paths])])
    values = json.loads(result.stdout)
    for path, value in zip(paths, values): value['path'] = path
    return values


def upload(path, target):
    assert target.startswith(BASE + '/')
    subprocess.run(SCP + [str(path), 'sbasadre@192.168.2.2:' + target], check=True, capture_output=True)


def main():
    # Resolve before mkdir/upload/docker bind mounts; reject existing path links.
    check = 'from pathlib import Path; import sys; p=Path(sys.argv[1]); assert p.is_absolute() and str(p.resolve()) == str(p)'
    remote(['/usr/bin/python3', '-c', check, BASE])
    LOCAL.mkdir(parents=True, exist_ok=True)
    remote(['mkdir', '-p', BASE + '/entrypoints', BASE + '/source'])
    originals = LOCAL / 'private-writer-entrypoints'
    originals.mkdir(exist_ok=True)
    hashes = {}
    for name in ['runtime.py', 'runtime_utils.py', 'worker_supervisor.py', 'service_supervisor.py', 'process_lock.py', 'runtime_worker.py']:
        actual = PROD + ('/workers/' if name == 'runtime_worker.py' else '/') + name
        source = remote(['cat', actual]).stdout
        destination = originals / name
        destination.write_bytes(source)
        hashes[name] = hashlib.sha256(source).hexdigest()
        upload(destination, BASE + '/entrypoints/' + name)

    archive = LOCAL / 'private-writer-candidate.tar'
    subprocess.run(['git', 'archive', '--format=tar', '-o', str(archive), 'HEAD'], cwd=REPO, check=True)
    with tarfile.open(archive, 'a') as stream:
        for name in ['tools/staging_private_json.py', 'tools/validate_private_state_staging.py', 'tools/rehearse_private_state_cutover.py', 'tests/test_staging_private_json.py']:
            stream.add(REPO / name, arcname=name)
    upload(archive, BASE + '/candidate.tar')
    remote(['tar', '-xf', BASE + '/candidate.tar', '-C', BASE + '/source'])

    # Only this isolated staging mount is writable to the provisioning container.
    provision = '''import os
    os.umask(0o077)
    for name in ["private-state", "mcp-cache", "evidence"]:
     p="/staging/"+name
     if not os.path.exists(p): os.mkdir(p,0o700)
     assert not os.listdir(p), "Provision only empty staging directories"
     os.chown(p,1026,1026)
     # Synology inherits ACL and ignores mkdir's requested mode here. This is
     # provisioning of EMPTY staging directories, not the writer's mechanism.
     os.chmod(p,0o700)
    '''
    remote([DOCKER, 'run', '--rm', '--network', 'none', '--read-only', '--user', '0:0',
            '-v', BASE + ':/staging:rw', '--entrypoint', 'python3', IMAGE, '-c', provision])
    preflight = acls([BASE + '/' + name for name in ['private-state', 'mcp-cache', 'evidence']])

    common = [DOCKER, 'run', '--rm', '--network', 'none', '--read-only', '--user', '1026:1026',
              '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--memory', '1g', '--cpuset-cpus', '0,1',
              '--tmpfs', '/tmp:rw,mode=1777', '--tmpfs', '/home/tenazas:rw,uid=1026,gid=1026,mode=0700',
              '-e', 'PYTHONPATH=/code:/home/tenazas:/home/tenazas/runtime', '-e', 'PYTHONDONTWRITEBYTECODE=1',
              '-e', 'TENAZAS_SUPERVISOR_MIN_INTERVAL=0',
              '-v', BASE + '/source:/code:ro', '-v', BASE + '/entrypoints:/entrypoints:ro',
              '-v', BASE + '/private-state:/private-state:rw', '-v', BASE + '/mcp-cache:/mcp-cache:rw']
    remote(common + ['-d', '--name', NAME, '--entrypoint', 'sh', IMAGE, '-c',
                     'exec tail -f /dev/null'])
    report = {'source_sha256': hashes, 'host_acl_preflight': preflight, 'cycles': []}
    try:
        prepared = remote([DOCKER, 'exec', NAME, 'python3',
                           '/code/tools/validate_private_state_staging.py', 'prepare'])
        report['prepare'] = json.loads(prepared.stdout)
        previous = None
        for cycle in range(1, 11):
            route = 'service' if cycle % 2 else 'dsm-exec-emulation'
            if route == 'service':
                args = ['python3', '/code/tools/validate_private_state_staging.py', 'service-cycle']
            else:
                args = ['python3', '/home/tenazas/runtime/runtime.py']
            result = remote([DOCKER, 'exec', NAME, *args])
            state_info = json.loads(remote([DOCKER, 'exec', NAME, 'python3', '-c',
                                  'import json,os; p="/private-state/state.json"; s=os.stat(p); '
                                  'd=json.load(open(p)); print(json.dumps({"inode":s.st_ino,"uid":s.st_uid,"gid":s.st_gid,"mode":oct(s.st_mode & 0o7777),"timestamp":d["ts"]}))']).stdout)
            assert state_info['inode'] != previous
            previous = state_info['inode']
            verification = json.loads(remote([DOCKER, 'exec', NAME, 'python3',
                                             '/code/tools/validate_private_state_staging.py', 'verify-cycle']).stdout)
            checked = acls([BASE + suffix for suffix in ['/private-state', '/private-state/state.json',
                       '/mcp-cache', '/mcp-cache/incidents', '/mcp-cache/state.json', '/mcp-cache/infra.json']])
            report['cycles'].append({'cycle': cycle, 'route': route, 'route_result': result.stdout.decode().strip(),
                                     'after_route': state_info, 'verification': verification, 'host_acl': checked})
            (LOCAL / 'private-writer-staging-evidence.json').write_text(json.dumps(report, indent=2))
            print(json.dumps({'cycle': cycle, 'route': route, 'mode': state_info['mode'],
                              'owner': [state_info['uid'], state_info['gid']], 'acl': 'Linux mode', 'ok': True}), flush=True)
        report['container_isolation'] = json.loads(remote([DOCKER, 'inspect', NAME]).stdout)[0]
    finally:
        # Stop this exact named staging container only; no production container actions.
        remote([DOCKER, 'stop', '-t', '2', NAME], check=False)

    probe = '''import json,os
    from pathlib import Path
    from workspace.runtime import mcp_readonly_adapter as adapter, mcp_cache_policy
    root=Path("/mcp-cache")
    if os.geteuid()==1027:
     for p in [root,root/"state.json",root/"infra.json"]:
      try:
       if p==root: list(p.iterdir())
       else: p.read_bytes()
      except PermissionError: pass
      else: raise AssertionError("Unauthorized read")
     print(json.dumps({"unauthorized_uid":1027,"read_denied":True}))
    else:
     mcp_cache_policy.validate_cache(root)
     adapter.CACHE_ROOT=root
     adapter.STATE_FILE=root/"state.json"
     adapter.AGENT_STATUS_FILE=root/"infra.json"
     adapter.INCIDENTS_DIR=root/"incidents"
     outputs=[adapter.call_tool(name) for name in adapter.TOOLS]
     assert "STAGING-PRIVATE-CREDENTIAL" not in json.dumps(outputs)
     try: (root/"state.json").write_text("invalid")
     except OSError as e: assert e.errno==30
     else: raise AssertionError("Gateway mount writable")
     print(json.dumps({"authorized_uid":1026,"six_read_operations":len(outputs),"mount_ro":True,"zero_secret_matches":True}))
    '''
    report['access_tests'] = []
    for uid in [1026, 1027]:
        args = [DOCKER, 'run', '--rm', '--network', 'none', '--read-only', '--user', str(uid)+':'+str(uid),
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '-e', 'PYTHONPATH=/code', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                '-v', BASE + '/source/workspace/runtime/mcp_readonly_adapter.py:/code/workspace/runtime/mcp_readonly_adapter.py:ro',
                '-v', BASE + '/source/workspace/runtime/mcp_cache_policy.py:/code/workspace/runtime/mcp_cache_policy.py:ro',
                '-v', BASE + '/mcp-cache:/mcp-cache:ro', '--entrypoint', 'python3', IMAGE, '-c', probe]
        value = json.loads(remote(args).stdout)
        report['access_tests'].append(value)
        print(json.dumps(value), flush=True)

    # Do not retain the inspect object's environment: only isolation facts.
    container = report.pop('container_isolation')
    report['container_isolation'] = {'image': container['Image'], 'user': container['Config']['User'],
     'network': container['HostConfig']['NetworkMode'], 'readonly_rootfs': container['HostConfig']['ReadonlyRootfs'],
     'port_bindings': container['HostConfig']['PortBindings'],
     'mounts': [{'source': m['Source'], 'destination': m['Destination'], 'rw': m['RW']} for m in container['Mounts']]}
    (LOCAL / 'private-writer-staging-evidence.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'completed':True,'cycles':len(report['cycles']),'evidence':'private-writer-staging-evidence.json'}))


if __name__ == "__main__":
    main()

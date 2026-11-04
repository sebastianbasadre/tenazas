"""Offline full-clone fixture. Never mount this over a production workspace.

Business code and OpenClaw are unchanged. Only external CLI probes are fixtures;
unknown commands fail closed. The container must have network=none and no sockets.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOME = Path('/home/tenazas')
RUNTIME = HOME / 'runtime'
PRIVATE = RUNTIME / 'private-state'
OLD = RUNTIME / 'state.json'


def check_root():
    if os.environ.get('TENAZAS_FULL_CLONE') != 'isolated-staging':
        raise RuntimeError('Explicit staging marker required')
    if Path('/fixture/ISOLATED_CLONE').read_text(encoding='utf-8').strip() != 'offline-private-state-fixture':
        raise RuntimeError('Isolated fixture sentinel required')
    if Path('/var/run/docker.sock').exists():
        raise RuntimeError('Docker socket forbidden in clone')


def seed():
    check_root()
    os.umask(0o077)
    (HOME / '.openclaw').mkdir(exist_ok=True)
    config = {'gateway': {'mode': 'local', 'bind': 'loopback',
              'auth': {'mode': 'token', 'token': 'synthetic-staging-token-only'}},
              'agents': {'defaults': {'workspace': str(HOME / '.openclaw/workspace')}}}
    (HOME / '.openclaw/openclaw.json').write_text(json.dumps(config), encoding='utf-8')
    (HOME / '.openclaw/workspace').mkdir(exist_ok=True)
    for name, data in {'state.json': {'ts': 'staging-baseline', 'containers': [],
                          'internet': {'ok': True}, 'vpn': {'ok': True}},
                       'events.json': [], 'tasks.json': [], 'memory_log.json': [],
                       'patterns.json': {}, 'nmap_cache.json': {'last_scan': time.time(), 'hosts': 2}}.items():
        (RUNTIME / name).write_text(json.dumps(data), encoding='utf-8')
    (RUNTIME / 'logs').mkdir(exist_ok=True)


def migrate():
    check_root()
    assert os.geteuid() == 0, 'Only staging provisioner prepares ownership'
    assert OLD.is_file() and not PRIVATE.exists()
    PRIVATE.mkdir(mode=0o700)
    os.chown(PRIVATE, 1026, 1026)
    os.chmod(PRIVATE, 0o700)  # empty directory: clear inherited Synology ACL
    os.replace(OLD, PRIVATE / 'state.json')
    os.chown(PRIVATE / 'state.json', 1026, 1026)
    os.chmod(PRIVATE / 'state.json', 0o600)  # one-time migration, not writer
    descriptor = os.open(OLD, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write('LEGACY_PRIVATE_STATE_FORBIDDEN\n')
    os.chown(OLD, 0, 0)
    os.chmod(OLD, 0)


def snapshot():
    check_root()
    target = PRIVATE / 'state.json' if PRIVATE.exists() else OLD
    data = json.loads(target.read_text(encoding='utf-8'))
    info = target.stat()
    result = {'time': time.time(), 'state': {'mode': oct(info.st_mode & 0o7777),
              'uid': info.st_uid, 'gid': info.st_gid, 'inode': info.st_ino,
              'timestamp': data.get('ts'), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()},
              'temporaries': [p.name for p in PRIVATE.glob('.private-json-*')] if PRIVATE.exists() else [],
              'service_state': {}, 'databases': {}, 'persistent_json': {}}
    if PRIVATE.exists():
        parent = PRIVATE.stat()
        assert (parent.st_mode & 0o7777, parent.st_uid, parent.st_gid) == (0o700, 1026, 1026)
        assert (info.st_mode & 0o7777, info.st_uid, info.st_gid) == (0o600, 1026, 1026)
        assert not result['temporaries']
    service = RUNTIME / 'logs/service_state.json'
    if service.exists():
        result['service_state'] = json.loads(service.read_text(encoding='utf-8'))
    # Never traverse runtime while gathering DB evidence: that would touch the
    # legacy tripwire from the evidence collector itself.
    databases = []
    for allowed in (HOME / '.openclaw/state', HOME / 'job-state'):
        if allowed.exists():
            databases.extend(allowed.rglob('*.sqlite'))
    for path in databases:
        connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        result['databases'][str(path.relative_to(HOME))] = {
            name: connection.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
            for name in tables}
        result['databases'][str(path.relative_to(HOME))]['__logical_sha256'] = hashlib.sha256(
            '\n'.join(connection.iterdump()).encode()).hexdigest()
        connection.close()
    for name in ('memory_log.json', 'user_profile.json', 'recent_context.json'):
        path = RUNTIME / name
        if path.exists():
            json.loads(path.read_text(encoding='utf-8'))
            result['persistent_json'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    sys.path[:0] = [str(RUNTIME), str(HOME)]
    from assistant_jobs import Jobs
    result['jobs'] = [{'id': job['id'], 'kind': job['kind'], 'status': job['status'],
                       'run_after': job['run_after'], 'updated': job['updated']}
                      for job in Jobs().list('staging-owner', limit=100)]
    return result


def jobs():
    check_root()
    sys.path[:0] = [str(RUNTIME), str(HOME)]
    from assistant_jobs import Jobs
    from document_library import LIBRARY
    store = Jobs()
    document = LIBRARY / 'staging-owner' / 'fixture.md'
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text('Private state staging: preserving all jobs and memory during rollback.', encoding='utf-8')
    slot = 'full-clone:' + str(time.time_ns()) + ':'
    return [store.create('staging-owner', kind, payload,
                        schedule_key=slot + kind, run_after=time.time() + delay)['id']
            for kind, payload, delay in [('weekly', {}, 0), ('diagnose', {'target': 'homelab'}, 90),
                                         ('daily', {}, 310),
                                         ('document_index', {'path': str(document), 'filename': document.name}, 120),
                                         ('documents', {'query': 'private staging rollback'}, 180)]]


def memory_rag():
    check_root()
    sys.path[:0] = [str(RUNTIME), str(HOME)]
    import user_memory
    user_memory.save_profile({'name': 'Synthetic staging owner', 'projects': ['private-state']})
    user_memory.add_context('Tenazas private-state staging', 'rag', 'Preserve memory on rollback')
    assert user_memory.get_recent_context()[-1]['summary'] == 'Preserve memory on rollback'
    workspace = HOME / '.openclaw/workspace'
    (workspace / 'staging-fixture.md').write_text(
        '# Private state staging\n' + 'Private state uses atomic writes and complete rollback preserves all jobs and memory. ' * 8,
        encoding='utf-8')
    script = workspace / 'skills/rag/ingest.py'
    result = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Real RAG ingester failed; inspect private staging logs')
    sys.path.insert(0, str(script.parent))
    import query
    retrieval = query.search('private state staging rollback')
    assert retrieval.get('results'), 'Real RAG search returned no documents'
    return {'memory_read_write': True, 'rag_ingest_exit': result.returncode,
            'rag_documents': len(retrieval['results'])}


def rollback():
    check_root()
    assert os.geteuid() == 0
    assert OLD.is_file() and PRIVATE.is_dir()
    before = json.loads((PRIVATE / 'state.json').read_text(encoding='utf-8'))
    OLD.unlink()
    os.replace(PRIVATE / 'state.json', OLD)
    PRIVATE.rmdir()
    metadata = Path('/fixture/baseline-state-metadata.json')
    if metadata.exists():
        original = json.loads(metadata.read_text(encoding='utf-8'))
        os.chown(OLD, original['uid'], original['gid'])
        os.chmod(OLD, original['mode'])
    assert json.loads(OLD.read_text(encoding='utf-8')) == before
    # Restore code and configuration only. Keep the newest JSON, jobs, memory,
    # SQLite/WAL and other persistent data accumulated during observation.
    backup = Path('/fixture/original')
    for directory in ('runtime', 'tools', 'scripts'):
        source = backup / directory
        if source.exists():
            for path in source.rglob('*'):
                if path.is_file():
                    destination = HOME / directory / path.relative_to(source)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
    for name in ('private_json.py', 'state_paths.py'):
        path = RUNTIME / name
        if path.exists() and not (backup / 'runtime' / name).exists():
            path.unlink()
    shutil.copyfile(backup / 'startup.sh', HOME / 'startup.sh')
    shutil.copyfile('/fixture/baseline-openclaw.json', HOME / '.openclaw/openclaw.json')
    skills = HOME / '.openclaw/workspace/skills'
    for path in Path('/fixture/original-skills').rglob('*'):
        if path.is_file():
            destination = skills / path.relative_to('/fixture/original-skills')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    return {'latest_state_preserved': True}


if __name__ == '__main__':
    action = sys.argv[1]
    value = {'seed': seed, 'migrate': migrate, 'snapshot': snapshot,
             'jobs': jobs, 'memory-rag': memory_rag, 'rollback': rollback}[action]()
    print(json.dumps(value))

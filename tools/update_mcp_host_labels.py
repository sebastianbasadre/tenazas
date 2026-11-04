"""Apply the reviewed host-label change to the active MCP adapter as root."""

import json
import os
import shutil
import subprocess
import socket
import time
from datetime import datetime, timezone
from pathlib import Path


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def main():
    if os.geteuid() != 0:
        raise SystemExit('Ejecutar con sudo.')
    config = json.loads(Path('/etc/tenazas-mcp-v2/producer.json').read_text())
    if config['ollama_url'].rstrip('/') != 'http://192.168.2.6:11434':
        raise SystemExit('La fuente de modelos no es HomeLab; revisar antes de cambiar etiquetas.')
    base = Path(run('systemctl', 'show', 'tenazas-mcp', '-p', 'WorkingDirectory', '--value'))
    if not str(base).startswith('/opt/tenazas-mcp/releases/'):
        raise SystemExit('Release inesperado.')
    target = base / 'workspace/runtime/mcp_readonly_adapter.py'
    if target.is_symlink():
        raise SystemExit('Adapter inesperado.')
    original = target.read_text()
    old_models = '        result = {k: state[k] for k in ("ollama_notebook", "ollama_nas", "model")}'
    new_models = '''        result = {
            "homelab": {
                "name": "HomeLab",
                "role": "model_host",
                "ollama": state["ollama_notebook"],
            },
            "nas": {"name": "NAS", "role": "tenazas_host"},
        }'''
    old_status = '        result["timestamp"] = state["generated_at"]'
    new_status = old_status + '''
        result["hosts"] = {
            "nas": {"name": "NAS", "role": "tenazas_host"},
            "homelab": {"name": "HomeLab", "role": "model_host"},
        }'''
    if original.count(old_models) != 1 or original.count(old_status) != 1:
        raise SystemExit('El adapter cambio o el parche ya esta aplicado; revisar.')
    updated = original.replace(old_models, new_models).replace(old_status, new_status)
    compile(updated, str(target), 'exec')
    backup = Path('/var/backups/tenazas-host-labels') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True, mode=0o700)
    saved = backup / target.name
    shutil.copy2(target, saved)
    stat = target.stat()

    def publish(content):
        temporary = target.with_name(target.name + '.host-labels-new')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, stat.st_mode & 0o777)
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, stat.st_uid, stat.st_gid)
        os.replace(temporary, target)

    try:
        publish(updated)
        probe = '''import json
from workspace.runtime import mcp_readonly_adapter as a
r=a.call_tool('get_models')
assert r['homelab']['role']=='model_host'
assert r['nas']['role']=='tenazas_host'
assert 'ollama_notebook' not in r
s=a.call_tool('get_status')
assert s['hosts']['nas']['role']=='tenazas_host'
print(json.dumps({'models_source':'HomeLab','tenazas_host':'NAS','snapshot':r['snapshot']['state']}))
'''
        # Use a new interpreter to verify the installed adapter and real cache.
        probe = "from pathlib import Path; from workspace.runtime import mcp_readonly_adapter as a; a.configure_cache(Path('/var/lib/tenazas-mcp-v2'));\n" + probe
        print(run('runuser', '-u', 'tenazas-mcp', '--', '/usr/bin/env', 'PYTHONPATH=' + str(base), '/usr/bin/python3', '-B', '-c', probe))
        run('systemctl', 'restart', 'tenazas-mcp')
        run('systemctl', 'is-active', '--quiet', 'tenazas-mcp')
        for attempt in range(30):
            try:
                with socket.create_connection(('127.0.0.1', 8443), timeout=1):
                    break
            except OSError:
                if attempt == 29:
                    raise
                time.sleep(1)
        run('systemctl', 'restart', 'tenazas-tunnel')
        run('systemctl', 'is-active', '--quiet', 'tenazas-tunnel')
    except Exception:
        publish(original)
        subprocess.run(['systemctl', 'restart', 'tenazas-mcp'], capture_output=True)
        subprocess.run(['systemctl', 'restart', 'tenazas-tunnel'], capture_output=True)
        raise
    print('Aplicado. Backup:', backup)


if __name__ == '__main__':
    main()

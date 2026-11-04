"""CLI-only external device fixtures; no execution of host commands."""
import json
import os
import sys
from pathlib import Path


def main():
    if os.environ.get('TENAZAS_FULL_CLONE') != 'isolated-staging':
        return 125
    name, args = Path(sys.argv[0]).name, sys.argv[1:]
    # Record command name only: URL/header arguments could contain credentials.
    with open('/home/tenazas/runtime/logs/fixture-probes.jsonl', 'a', encoding='utf-8') as stream:
        stream.write(json.dumps({'command': name}) + '\n')
    if name == 'ping':
        print('64 bytes from 203.0.113.1: time=1.0 ms')
    elif name == 'nmap':
        print('Host is up\nHost is up')
    elif name == 'docker' and args[:1] == ['ps']:
        print('openclaw\tUp 1 hour\npiper\tUp 1 hour')
    elif name == 'curl':
        urls = [value for value in args if value.startswith(('http://', 'https://'))]
        if len(urls) != 1:
            return 125
        url = urls[0]
        if 'ifconfig.me' in url:
            print('203.0.113.7')
        elif 'ip-api.com' in url:
            print('{"countryCode":"US"}')
        elif '/api/tags' in url:
            print('{"models":[{"name":"staging-fixture"}]}')
        elif 'api.telegram.org' in url:
            print('{"ok":true,"result":[]}')
        else:
            return 125
    else:
        return 125
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

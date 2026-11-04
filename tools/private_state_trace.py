"""Fail-closed review of strace -yy file traces from the isolated clone.

An absolute legacy lookup is a hit even when stat succeeds or open fails. Bare
state.json is accepted only with an explicitly resolved private-state dirfd;
otherwise it is ambiguous and prevents a clean observation claim.
"""
import re
from pathlib import Path

LEGACY = '/home/tenazas/runtime/state.json'
PRIVATE = '/home/tenazas/runtime/private-state'


def classify(line):
    strings = re.findall(r'"((?:[^"\\]|\\.)*)"', line)
    for value in strings:
        if value == LEGACY or value.startswith(LEGACY + '/'):
            return 'legacy'
        if value == 'state.json' and '<' + PRIVATE + '>' not in line:
            return 'unresolved-relative-state'
        if value.endswith('/runtime/state.json') and value != LEGACY:
            return 'alternate-runtime-state'
    return None


def review(directory, prefix):
    files = sorted(Path(directory).glob(prefix + '.*'))
    if not files:
        raise RuntimeError('Missing native traces: observation cannot pass')
    hits = []
    for path in files:
        for number, line in enumerate(path.read_text(encoding='utf-8', errors='strict').splitlines(), 1):
            classification = classify(line)
            if classification:
                hits.append({'trace': path.name, 'line': number, 'classification': classification})
    return {'trace_files': len(files), 'hits': hits, 'clean': not hits}

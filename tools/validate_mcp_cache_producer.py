"""Isolated trusted-producer probe. Credential inventory enters only via stdin.

Never run against a writable production destination. Stdout contains counts only.
"""

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

from workspace.runtime.mcp_cache_producer import credential_variants, export_cache


def main():
    source, destination = map(Path, sys.argv[1:3])
    credentials = json.load(sys.stdin)["secret_values"]
    raw = source.read_bytes()
    values = [value for value in credentials if isinstance(value, str) and value]
    decoded_raw = json.dumps(json.loads(raw), ensure_ascii=False)
    raw_matches = sum(any(variant in decoded_raw for variant in credential_variants(value)) for value in values)
    summary = export_cache(json.loads(raw), secret_values=values, destination=destination)
    files = list(destination.rglob("*.json"))
    variants = {variant for value in values for variant in credential_variants(value)}
    exported_matches = sum(any(value in path.read_text() for value in variants) for path in files)
    assert exported_matches == 0
    assert not os.access(source, os.W_OK)
    print(json.dumps({"uid": os.getuid(), "gid": os.getgid(),
                      "source_read_only": True, "source_sha256": hashlib.sha256(raw).hexdigest(),
                      "source_mode": oct(stat.S_IMODE(source.stat().st_mode)),
                      "credential_values_checked": len(values), "raw_credential_matches": raw_matches,
                      "exported_credential_matches": exported_matches,
                      "cache_mode": oct(stat.S_IMODE(destination.stat().st_mode)),
                      "cache_file_modes": sorted({oct(stat.S_IMODE(path.stat().st_mode)) for path in files}),
                      **summary}))


if __name__ == "__main__":
    main()

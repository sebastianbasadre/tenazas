"""VM-side fixed SSH capability to the NAS job assistant. No Gmail credentials."""

import json
import os
import stat
import subprocess
from pathlib import Path

from workspace.runtime.mcp_applications_contract import validate


class ApplicationsClient:
    def __init__(self, config):
        value = json.loads(Path(config).read_bytes())
        if set(value) != {"key_file", "known_hosts"}:
            raise ValueError("Invalid applications configuration")
        self.key = Path(value["key_file"])
        self.known_hosts = Path(value["known_hosts"])
        for path in (self.key, self.known_hosts):
            if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file():
                raise ValueError("Unsafe SSH capability")
        info = self.key.stat()
        if os.name == "posix" and (
            stat.S_IMODE(info.st_mode) not in {0o400, 0o600} or info.st_uid != os.geteuid()
        ):
            raise ValueError("Unsafe SSH key permissions")

    def call(self, name, arguments):
        validate(name, arguments)
        result = subprocess.run(
            [
                "/usr/bin/ssh",
                "-F",
                "/dev/null",
                "-T",
                "-p",
                "4822",
                "-i",
                str(self.key),
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=3",
                "-o",
                "UpdateHostKeys=no",
                "-o",
                "UserKnownHostsFile=" + str(self.known_hosts),
                "sbasadre@192.168.2.2",
            ],
            input=json.dumps({"name": name, "arguments": arguments}).encode(),
            capture_output=True,
            timeout=4,
            check=False,
        )
        if result.returncode or len(result.stdout) > 1024**2:
            raise ValueError("Application service unavailable")
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or set(value) != {"ok", "data"}:
            raise ValueError("Invalid application service response")
        return value

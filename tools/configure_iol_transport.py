"""Back up the local IOL skill and use the versioned HTTP transport."""

import shutil
from pathlib import Path


def main():
    root = Path("/home/tenazas/.openclaw")
    path = root / "workspace/skills/iol_broker/iol_client.py"
    before = "from urllib.request import Request, urlopen"
    after = "from urllib.request import Request\nfrom iol_http import urlopen"
    content = path.read_text()
    if after in content:
        print("IOL transport already configured")
        return
    if before not in content:
        raise RuntimeError("Unrecognized IOL skill; no changes made")
    backup = root / "deployments/20261003-24x7/iol_client.before.py"
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(content.replace(before, after, 1))
    print("IOL skill uses versioned standard HTTP transport")


if __name__ == "__main__":
    main()

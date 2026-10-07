"""NAS-only inventory source; no remote execution, credentials or VM Docker access."""

import json
import subprocess
from datetime import UTC, datetime

from workspace.runtime.mcp_snapshot_contract import MAX_ITEMS, public_text


def inventory(run=subprocess.run):
    result = run(
        [
            "/usr/local/bin/docker",
            "ps",
            "-a",
            "--format",
            '{"Names":{{json .Names}},"State":{{json .State}}}',
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    rows = result.stdout.splitlines()
    if len(rows) > MAX_ITEMS:
        raise ValueError("Inventory limit")
    items = []
    for row in rows:
        raw = json.loads(row)
        state = raw.get("State")
        up = state == "running"
        status = (
            "running"
            if up
            else "stopped"
            if state in {"exited", "created", "paused", "dead"}
            else "unknown"
        )
        items.append({"name": public_text(raw["Names"]), "up": up, "status": status})
    return {
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "containers": items,
    }


if __name__ == "__main__":
    print(json.dumps(inventory(), allow_nan=False))

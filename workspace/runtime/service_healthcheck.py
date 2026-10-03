"""Docker readiness: gateway plus a recent supervisor snapshot."""

import json
import time
from pathlib import Path
from urllib.request import urlopen


def main():
    state = json.loads((Path(__file__).parent / "logs/service_state.json").read_text())
    if time.time() - state["ts"] > 90:
        return 1
    required = ["gateway", "telegram_router", "metrics"]
    if not all(state["services"].get(name, {}).get("running") for name in required):
        return 1
    with urlopen("http://127.0.0.1:18789/health", timeout=5) as response:
        return 0 if response.status == 200 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError):
        raise SystemExit(1) from None

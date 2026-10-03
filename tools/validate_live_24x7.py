"""Check local inference and optionally recovery of the metrics exporter."""

import argparse
import json
import os
import signal
import time
from pathlib import Path
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fault-metrics", action="store_true")
    args = parser.parse_args()
    results = {}
    for name, port, model in (
        ("gpu", 18902, "qwen3-fast:8b"),
        ("cpu", 11434, "qwen2.5:3b"),
    ):
        payload = {"model": model, "prompt": "Responde OK", "stream": False,
                   "options": {"num_predict": 8, "num_ctx": 2048}}
        request = Request(f"http://127.0.0.1:{port}/api/generate",
                          data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=120) as response:
            data = json.load(response)
        results[name] = bool(data.get("done") and data.get("response"))
    state_path = Path("/home/tenazas/runtime/logs/service_state.json")
    state = json.loads(state_path.read_text())
    if args.fault_metrics:
        old = state["services"]["metrics"]
        pid = old["pid"]
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
        if b"metrics_server.py" not in command:
            raise RuntimeError("Refusing to signal an unrelated process")
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 45
        results["metrics_recovery"] = False
        while time.monotonic() < deadline:
            time.sleep(2)
            current = json.loads(state_path.read_text())["services"]["metrics"]
            if current["running"] and current["pid"] != pid:
                with urlopen("http://127.0.0.1:9105/metrics", timeout=3) as response:
                    results["metrics_recovery"] = response.status == 200
                break
    print(json.dumps(results))
    if not all(results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Passive 24-hour observation of service state; no fault injection or API actions."""

import argparse
import json
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--root", type=Path, default=Path("/home/tenazas"))
    args = parser.parse_args()
    end = time.monotonic() + args.hours * 3600
    output = args.root / ".openclaw/state/24x7-observation.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    while time.monotonic() < end:
        sample = {"ts": time.time()}
        for label, path in {
            "services": args.root / "runtime/logs/service_state.json",
            "runtime": args.root / "runtime/supervisor_state.json",
            "agents": args.root / "runtime/agents_state.json",
        }.items():
            try:
                data = json.loads(path.read_text())
                if label == "agents":
                    data = {name: {key: item.get(key) for key in ("last_run", "last_success", "ok")}
                            for name, item in data.items()}
                elif label == "runtime":
                    data = {"ts": data.get("ts"), "workers": [
                        {key: worker.get(key) for key in ("name", "ok", "elapsed")}
                        for worker in data.get("workers", [])]}
                sample[label] = data
            except (OSError, ValueError):
                sample[label] = {"missing": True}
        with output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample) + "\n")
        time.sleep(min(60, max(0, end - time.monotonic())))


if __name__ == "__main__":
    main()

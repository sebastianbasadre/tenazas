"""Passive 24-hour observation of service state; no fault injection or API actions."""

import argparse
import json
import time
import sqlite3
from pathlib import Path
from summarize_observation import summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--root", type=Path, default=Path("/home/tenazas"))
    args = parser.parse_args()
    output = args.root / ".openclaw/state/24x7-observation.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    if output.exists():
        for line in output.read_text().splitlines():
            try:
                entry = json.loads(line)
                if entry.get("schema_version") == 2:
                    start = entry["ts"]
                    break
            except ValueError:
                continue
    end = start + args.hours * 3600
    report_path = output.with_name("24x7-report.json")
    while time.time() < end:
        sample = {"ts": time.time(), "schema_version": 2}
        for label, path in {
            "services": args.root / "runtime/logs/service_state.json",
            "runtime": args.root / "runtime/supervisor_state.json",
            "agents": args.root / "runtime/agents_state.json",
            "inference": args.root / "runtime/logs/inference_state.json",
            "agent_metrics": args.root / "runtime/logs/agent_metrics_state.json",
        }.items():
            try:
                data = json.loads(path.read_text())
                if label == "agents":
                    data = {name: {key: item.get(key) for key in ("last_run", "last_success", "ok")}
                            for name, item in data.items()}
                elif label == "runtime":
                    data = {"ts": data.get("ts"), "last_success_ts": data.get("last_success_ts"), "workers": [
                        {key: worker.get(key) for key in ("name", "ok", "elapsed")}
                        for worker in data.get("workers", [])]}
                sample[label] = data
            except (OSError, ValueError):
                sample[label] = {"missing": True}
        try:
            connection = sqlite3.connect(f"file:{args.root}/job-state/jobs.sqlite?mode=ro", uri=True)
            try:
                sample["jobs"] = dict(connection.execute("SELECT status, count(*) FROM jobs GROUP BY status"))
                sample["jobs"]["awaiting_crash_review"] = connection.execute(
                    "SELECT count(*) FROM jobs WHERE status='interrupted' OR (status='running' AND lease_until < ?)",
                    (time.time(),),
                ).fetchone()[0]
            finally:
                connection.close()
        except sqlite3.Error:
            sample["jobs"] = {"missing": True}
        with output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample) + "\n")
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(summarize(output), indent=2))
        temporary.replace(report_path)
        time.sleep(min(60, max(0, end - time.time())))
    report_path.write_text(json.dumps(summarize(output), indent=2))


if __name__ == "__main__":
    main()

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from summarize_observation import summarize


def test_repeated_samples_do_not_inflate_completed_cycles(tmp_path):
    path = tmp_path / "observation.jsonl"
    sample = {"ts": time.time(), "schema_version": 2,
              "runtime": {"ts": "cycle-one", "workers": [{"name": "agents", "ok": True}]},
              "jobs": {"succeeded": 1}}
    path.write_text(json.dumps(sample) + "\n" + json.dumps(sample) + "\n")
    report = summarize(path)
    assert report["cycles_observed"] == report["successful_cycles"] == 1
    assert not report["period_complete"]


def test_report_retains_failed_workers_and_legacy_restarts(tmp_path):
    path = tmp_path / "observation.jsonl"
    path.write_text(json.dumps({"ts": time.time(),
        "services": {"services": {"metrics": {"restarts": 1}}},
        "runtime": {"ts": "failed-cycle", "workers": [{"name": "agents", "ok": False}]},
        "jobs": {"interrupted": 1}}) + "\n")
    report = summarize(path)
    assert report["successful_cycles"] == 0
    assert report["failed_worker_runs"][0]["worker"] == "agents"
    assert report["legacy_restart_maxima"]["metrics"] == 1
    assert report["latest_job_states"]["interrupted"] == 1

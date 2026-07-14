#!/usr/bin/env python3
"""
n8n_workflow_tuner.py

Decision: Python stdlib script instead of .mjs because the repo already uses
Python tooling heavily, and urllib/json/shutil are enough for this API task
without adding Node package assumptions.

Tunes OpenClaw n8n workflow schedules through the n8n REST API.
Requires N8N_API_KEY in the environment.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = os.environ.get("N8N_API_BASE", "http://192.168.2.2:5678/api/v1").rstrip("/")
ROOT = Path(__file__).resolve().parent
BACKUP_DIR = ROOT / "n8n_backups"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "n8n_tuner.log"


TARGETS = {
    "OpenClaw Notification Calendar": ("minutes", 30, None),
    "OpenClaw Notification Email": ("minutes", 15, {"hours": {"start": 8, "end": 23}}),
    "OpenClaw GPU Keepalive": ("minutes", 20, None),
    "OpenClaw Autofix Agent": ("minutes", 15, None),
    "OpenClaw Proposal Executor": ("minutes", 30, None),
    "OpenClaw Portfolio Reconciler": ("minutes", 30, None),
}

UNCHANGED = {"OpenClaw IOL Refresh"}
SCHEDULE_TYPE = "n8n-nodes-base.scheduleTrigger"
CRON_TYPE = "n8n-nodes-base.cron"


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def api_key() -> str:
    key = os.environ.get("N8N_API_KEY")
    if not key:
        raise SystemExit("N8N_API_KEY is required")
    return key


def request_json(method: str, path: str, body: Any | None = None) -> Any:
    data = None
    headers = {
        "Accept": "application/json",
        "X-N8N-API-KEY": api_key(),
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {detail}") from exc


def update_workflow(workflow_id: str, body: dict[str, Any]) -> str:
    """Update a workflow. n8n 2.19.5 rejects PATCH on some installs, so use PUT fallback."""
    try:
        request_json("PATCH", f"/workflows/{workflow_id}", body)
        return "PATCH"
    except RuntimeError as exc:
        if "HTTP 405" not in str(exc):
            raise
        log(f"WARN PATCH not allowed for workflow {workflow_id}; retrying with PUT")
        request_json("PUT", f"/workflows/{workflow_id}", body)
        return "PUT"


def list_workflows() -> list[dict[str, Any]]:
    payload = request_json("GET", "/workflows")
    if isinstance(payload, dict):
        return list(payload.get("data", []))
    if isinstance(payload, list):
        return payload
    raise RuntimeError(f"Unexpected workflow list payload: {type(payload).__name__}")


def backup_workflow(workflow: dict[str, Any]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    safe_id = str(workflow.get("id", "unknown")).replace("/", "_")
    path = BACKUP_DIR / f"{safe_id}_{ts}.json"
    path.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def schedule_interval(field: str, interval: int, extra: dict[str, Any] | None) -> dict[str, Any]:
    if field == "minutes":
        if extra and "hours" in extra:
            start = int(extra["hours"]["start"])
            end = int(extra["hours"]["end"])
            return {"field": "cronExpression", "expression": f"*/{interval} {start}-{end} * * *"}
        return {"field": "cronExpression", "expression": f"*/{interval} * * * *"}

    item: dict[str, Any] = {"field": field}
    if field == "hours":
        item["hoursInterval"] = interval
    elif field == "days":
        item["daysInterval"] = interval
    else:
        item["interval"] = interval
    if extra:
        item.update(extra)
    return item


def cron_parameters(minutes: int, extra: dict[str, Any] | None) -> dict[str, Any]:
    if extra and "hours" in extra:
        start = int(extra["hours"]["start"])
        end = int(extra["hours"]["end"])
        expression = f"*/{minutes} {start}-{end} * * *"
    else:
        expression = f"*/{minutes} * * * *"
    return {"triggerTimes": {"item": [{"mode": "custom", "cronExpression": expression}]}}


def mutate_trigger(node: dict[str, Any], field: str, interval: int, extra: dict[str, Any] | None) -> tuple[bool, str, str]:
    before = json.dumps(node.get("parameters", {}), sort_keys=True, ensure_ascii=False)
    params = copy.deepcopy(node.get("parameters") or {})
    ntype = node.get("type")

    if ntype == SCHEDULE_TYPE:
        params["rule"] = {"interval": [schedule_interval(field, interval, extra)]}
    elif ntype == CRON_TYPE:
        params = cron_parameters(interval, extra)
    else:
        return False, before, before

    after = json.dumps(params, sort_keys=True, ensure_ascii=False)
    if before == after:
        return False, before, after
    node["parameters"] = params
    return True, before, after


def build_patch(workflow: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "name",
        "nodes",
        "connections",
        "settings",
    )
    patch = {key: workflow[key] for key in allowed if key in workflow}
    settings = workflow.get("settings") or {}
    patch["settings"] = {"executionOrder": settings.get("executionOrder", "v1")}
    return patch


def verify(workflow_id: str, expected_nodes: list[dict[str, Any]]) -> bool:
    current = request_json("GET", f"/workflows/{workflow_id}")
    by_id = {node.get("id"): node for node in current.get("nodes", [])}
    by_name = {node.get("name"): node for node in current.get("nodes", [])}
    for node in expected_nodes:
        found = by_id.get(node.get("id")) or by_name.get(node.get("name"))
        if not found:
            return False
        if found.get("parameters") != node.get("parameters"):
            return False
    return True


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    log(f"Starting n8n tuner against {BASE_URL}")

    changed = 0
    skipped = 0
    workflows = [wf for wf in list_workflows() if wf.get("name", "").startswith("OpenClaw")]
    log(f"Found {len(workflows)} OpenClaw workflows")

    for item in workflows:
        workflow_id = str(item["id"])
        workflow = request_json("GET", f"/workflows/{workflow_id}")
        name = workflow.get("name", "")

        if name in UNCHANGED:
            log(f"SKIP unchanged by policy: {name}")
            skipped += 1
            continue
        if name not in TARGETS:
            log(f"WARN no target rule for workflow: {name}")
            skipped += 1
            continue

        trigger_nodes = [n for n in workflow.get("nodes", []) if n.get("type") in {SCHEDULE_TYPE, CRON_TYPE}]
        if not trigger_nodes:
            log(f"WARN no schedule/cron trigger found: {name}")
            skipped += 1
            continue

        field, interval, extra = TARGETS[name]
        backup = backup_workflow(workflow)
        expected_nodes: list[dict[str, Any]] = []
        mutated = False
        before_after: list[tuple[str, str, str]] = []

        for node in trigger_nodes:
            did_change, before, after = mutate_trigger(node, field, interval, extra)
            if did_change:
                mutated = True
                expected_nodes.append(copy.deepcopy(node))
                before_after.append((node.get("name", "<unnamed>"), before, after))

        if not mutated:
            log(f"OK already tuned: {name} backup={backup}")
            skipped += 1
            continue

        method = update_workflow(workflow_id, build_patch(workflow))
        if not verify(workflow_id, expected_nodes):
            raise RuntimeError(f"Verification failed after PATCH for {name}")

        for node_name, before, after in before_after:
            log(f"CHANGED {name} method={method} node={node_name} before={before} after={after} backup={backup}")
        changed += 1

    log(f"Done. changed={changed} skipped={skipped}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"ERROR {exc}")
        raise

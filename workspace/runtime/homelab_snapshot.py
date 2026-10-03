#!/usr/bin/env python3
"""
homelab_snapshot.py
Unified homelab state snapshot — single payload for Telegram, logs, and metrics.

Collects:
  - Containers: total, running, unhealthy, stopped
  - Ollama: status, loaded models, VRAM usage
  - GPU: temperature, load%, VRAM total
  - RAM/CPU: NAS usage
  - Disk: /volume1 space
  - VPN: gluetun status, exit IP, country (must be ES)
  - Skills: last 5 used + timestamp
  - Open incidents: from state/incidents/
  - Pending approvals: from state/approval_queue.json

Usage:
  python3 homelab_snapshot.py --status   # print + send to Telegram
  python3 homelab_snapshot.py --json     # print JSON payload only
"""

import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from .runtime_config import HOMELAB_IP, HOMELAB_USER, OLLAMA_GPU_URL, get_default_telegram_chat_id
except ImportError:
    from runtime_config import HOMELAB_IP, HOMELAB_USER, OLLAMA_GPU_URL, get_default_telegram_chat_id

log = logging.getLogger("homelab_snapshot")

RUNTIME = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state")
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")
OLLAMA_URL = OLLAMA_GPU_URL
CHAT_ID = get_default_telegram_chat_id()
LOG_PATH = RUNTIME / "logs" / "homelab_snapshot.log"


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")


def _notify(msg: str):
    try:
        token = json.loads(CONFIG_PATH.read_text())["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=payload, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception as e:
        _log({"action": "notify_error", "error": str(e)})


# ── Collectors ─────────────────────────────────────────────────

def _collect_containers() -> dict:
    """Docker container status."""
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            raise RuntimeError("docker ps failed")
        lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
        total = len(lines)
        running = sum(1 for l in lines if "\t" in l and l.split("\t", 1)[1].startswith("Up"))
        unhealthy = sum(1 for l in lines if "\t" in l and "unhealthy" in l.split("\t", 1)[1].lower())
        stopped = total - running
        return {
            "total": total, "running": running,
            "unhealthy": unhealthy, "stopped": stopped,
        }
    except Exception as e:
        _log({"collector": "containers", "error": str(e)})
        return {"total": 0, "running": 0, "unhealthy": 0, "stopped": 0, "error": str(e)}


def _collect_ollama() -> dict:
    """Ollama status, loaded models, VRAM."""
    result = {"status": "offline", "models_loaded": [], "vram_used_mb": 0, "vram_free_mb": 0}
    try:
        # Check if Ollama is responding
        resp = urlopen(Request(f"{OLLAMA_URL}/api/tags"), timeout=5)
        tags = json.loads(resp.read())
        result["status"] = "online"
        result["models_available"] = [m["name"] for m in tags.get("models", [])]

        # Get loaded models (in VRAM)
        resp2 = urlopen(Request(f"{OLLAMA_URL}/api/ps"), timeout=5)
        ps = json.loads(resp2.read())
        loaded = ps.get("models", [])
        result["models_loaded"] = [m["name"] for m in loaded]

        # Calculate VRAM from loaded models
        total_vram = 0
        for m in loaded:
            total_vram += m.get("size", 0)
        result["vram_used_mb"] = round(total_vram / (1024 * 1024))
    except Exception as e:
        _log({"collector": "ollama", "error": str(e)})
        result["error"] = str(e)
    return result


def _collect_gpu() -> dict:
    """GPU temperature, load, VRAM via nvidia-smi on HomeLab."""
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
             f"{HOMELAB_USER}@{HOMELAB_IP}",
             "nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split(",")
            if len(parts) >= 4:
                return {
                    "temp_c": int(parts[0].strip()),
                    "load_pct": int(parts[1].strip()),
                    "vram_used_mb": int(parts[2].strip()),
                    "vram_total_mb": int(parts[3].strip()),
                    "vram_free_mb": int(parts[3].strip()) - int(parts[2].strip()),
                }
    except Exception as e:
        _log({"collector": "gpu", "error": str(e)})
    return {"temp_c": 0, "load_pct": 0, "vram_used_mb": 0, "vram_total_mb": 0, "vram_free_mb": 0, "error": "unavailable"}


def _collect_ram_cpu() -> dict:
    """NAS RAM and CPU usage."""
    result = {"ram_used_mb": 0, "ram_total_mb": 0, "ram_pct": 0, "cpu_pct": 0}
    try:
        # RAM from /proc/meminfo
        r = subprocess.run(["cat", "/proc/meminfo"], capture_output=True, text=True, timeout=5)
        meminfo = {}
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip()
                val = parts[1].strip().split()[0]
                meminfo[key] = int(val)
        total_kb = meminfo.get("MemTotal", 0)
        avail_kb = meminfo.get("MemAvailable", 0)
        used_kb = total_kb - avail_kb
        result["ram_total_mb"] = round(total_kb / 1024)
        result["ram_used_mb"] = round(used_kb / 1024)
        result["ram_pct"] = round(used_kb / total_kb * 100) if total_kb else 0
    except Exception as e:
        _log({"collector": "ram", "error": str(e)})
        result["ram_error"] = str(e)

    try:
        # CPU from /proc/loadavg (1-min load average)
        r = subprocess.run(["cat", "/proc/loadavg"], capture_output=True, text=True, timeout=5)
        load1 = float(r.stdout.split()[0])
        # Get CPU count
        r2 = subprocess.run(["nproc"], capture_output=True, text=True, timeout=5)
        ncpu = int(r2.stdout.strip()) if r2.returncode == 0 else 4
        result["cpu_pct"] = round(load1 / ncpu * 100)
        result["cpu_load_1m"] = load1
        result["cpu_cores"] = ncpu
    except Exception as e:
        _log({"collector": "cpu", "error": str(e)})
        result["cpu_error"] = str(e)
    return result


def _collect_disk() -> dict:
    """Disk usage on /volume1."""
    try:
        r = subprocess.run(["df", "-m", "/volume1"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 4:
                total_mb = int(parts[1])
                used_mb = int(parts[2])
                free_mb = int(parts[3])
                pct = round(used_mb / total_mb * 100) if total_mb else 0
                return {
                    "total_gb": round(total_mb / 1024),
                    "used_gb": round(used_mb / 1024),
                    "free_gb": round(free_mb / 1024),
                    "used_pct": pct,
                }
    except Exception as e:
        _log({"collector": "disk", "error": str(e)})
    return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "used_pct": 0, "error": "unavailable"}


def _collect_vpn() -> dict:
    """VPN status via gluetun container."""
    result = {"status": "unknown", "ip": "", "country": ""}
    try:
        # Check gluetun container
        r = subprocess.run(
            ["docker", "ps", "--filter", "name=gluetun", "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=5,
        )
        if "Up" in r.stdout:
            result["status"] = "up"
        else:
            result["status"] = "down"
            return result

        # Get exit IP via gluetun API
        try:
            resp = urlopen(Request("http://localhost:8000/v1/publicip/ip"), timeout=5)
            data = json.loads(resp.read())
            result["ip"] = data.get("public_ip", data.get("ip", ""))
        except Exception:
            pass

        # Get country
        try:
            resp = urlopen(Request("http://localhost:8000/v1/publicip/country"), timeout=5)
            data = json.loads(resp.read())
            result["country"] = data.get("country", "")
        except Exception:
            pass

        result["is_es"] = result.get("country", "").upper() in ("ES", "SPAIN", "ESPANA")
    except Exception as e:
        _log({"collector": "vpn", "error": str(e)})
        result["error"] = str(e)
    return result


def _collect_recent_skills() -> list:
    """Last 5 skills used from telegram_router log."""
    log_file = RUNTIME / "logs" / "telegram_router.log"
    skills = []
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        for line in reversed(lines):
            if len(skills) >= 5:
                break
            try:
                entry = json.loads(line)
                if entry.get("action") == "intercepted" and entry.get("category"):
                    skills.append({
                        "skill": entry["category"],
                        "ts": entry.get("ts", ""),
                    })
            except (json.JSONDecodeError, KeyError):
                continue
    except FileNotFoundError:
        pass
    return skills


def _collect_open_incidents() -> list:
    """Open incidents from state/incidents/."""
    incidents_dir = STATE_DIR / "incidents"
    open_incidents = []
    if not incidents_dir.exists():
        return open_incidents
    try:
        for f in sorted(incidents_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                inc = json.loads(f.read_text())
                if inc.get("status") in ("detected", "pending_approval", "executing"):
                    open_incidents.append({
                        "id": inc.get("incident_id", f.stem),
                        "title": inc.get("title", ""),
                        "severity": inc.get("severity", ""),
                        "status": inc.get("status", ""),
                    })
            except Exception:
                continue
    except Exception as e:
        _log({"collector": "incidents", "error": str(e)})
    return open_incidents


def _collect_pending_approvals() -> list:
    """Pending approvals from approval_queue.json."""
    queue_path = STATE_DIR / "approval_queue.json"
    pending = []
    try:
        if queue_path.exists():
            queue = json.loads(queue_path.read_text())
            now = time.time()
            for entry in queue:
                if entry.get("status") == "pending" and now < entry.get("expires_at", 0):
                    pending.append({
                        "id": entry.get("id", ""),
                        "action": entry.get("action_name", ""),
                        "target": entry.get("target", ""),
                        "risk": entry.get("risk_level", ""),
                    })
    except Exception as e:
        _log({"collector": "approvals", "error": str(e)})
    return pending


# ── Main API ───────────────────────────────────────────────────

def snapshot() -> dict:
    """Collect full homelab snapshot. Never crashes — logs and continues on error."""
    collectors = {
        "containers": _collect_containers,
        "ollama": _collect_ollama,
        "gpu": _collect_gpu,
        "ram_cpu": _collect_ram_cpu,
        "disk": _collect_disk,
        "vpn": _collect_vpn,
        "recent_skills": _collect_recent_skills,
        "open_incidents": _collect_open_incidents,
        "pending_approvals": _collect_pending_approvals,
    }

    result = {"ts": datetime.now().astimezone().isoformat()}
    for name, fn in collectors.items():
        try:
            result[name] = fn()
        except Exception as e:
            _log({"collector": name, "error": str(e)})
            result[name] = {"error": str(e)}

    _log({"action": "snapshot_complete"})
    return result


def format_telegram(snap: dict) -> str:
    """Format snapshot as a compact Telegram message."""
    lines = ["Estado HomeLab"]
    now_str = datetime.now().strftime("%H:%M %d/%m")
    lines.append(f"({now_str})\n")

    # Containers
    c = snap.get("containers", {})
    c_icon = "" if c.get("unhealthy", 0) == 0 and c.get("stopped", 0) == 0 else ""
    lines.append(f"Docker: {c.get('running', 0)}/{c.get('total', 0)} running"
                 + (f", {c['unhealthy']} unhealthy" if c.get("unhealthy") else "")
                 + (f", {c['stopped']} stopped" if c.get("stopped") else ""))

    # Ollama
    o = snap.get("ollama", {})
    o_status = o.get("status", "offline")
    loaded = o.get("models_loaded", [])
    lines.append(f"Ollama: {o_status}" + (f" ({', '.join(loaded)})" if loaded else ""))

    # GPU
    g = snap.get("gpu", {})
    if not g.get("error"):
        lines.append(f"GPU: {g.get('temp_c', '?')}C, {g.get('load_pct', '?')}% load, "
                     f"VRAM {g.get('vram_used_mb', 0)}/{g.get('vram_total_mb', 0)}MB")

    # RAM/CPU
    rc = snap.get("ram_cpu", {})
    lines.append(f"NAS: RAM {rc.get('ram_pct', '?')}% ({rc.get('ram_used_mb', 0)}/"
                 f"{rc.get('ram_total_mb', 0)}MB), CPU {rc.get('cpu_pct', '?')}%")

    # Disk
    d = snap.get("disk", {})
    if not d.get("error"):
        lines.append(f"Disco: {d.get('used_gb', 0)}/{d.get('total_gb', 0)}GB "
                     f"({d.get('used_pct', 0)}% usado, {d.get('free_gb', 0)}GB libre)")

    # VPN
    v = snap.get("vpn", {})
    vpn_icon = "" if v.get("is_es") else ""
    vpn_country = v.get("country", "?")
    lines.append(f"VPN: {v.get('status', '?')} — {vpn_country}"
                 + (f" ({v.get('ip', '')})" if v.get("ip") else ""))

    # Skills
    skills = snap.get("recent_skills", [])
    if skills:
        skill_list = ", ".join(s["skill"].replace("skill:", "") for s in skills[:5])
        lines.append(f"Skills recientes: {skill_list}")

    # Incidents
    incidents = snap.get("open_incidents", [])
    if incidents:
        lines.append(f"\nIncidentes abiertos ({len(incidents)}):")
        for inc in incidents[:5]:
            lines.append(f"  [{inc['severity']}] {inc['title']}")
    else:
        lines.append("Incidentes: 0 abiertos")

    # Pending approvals
    approvals = snap.get("pending_approvals", [])
    if approvals:
        lines.append(f"\nAprobaciones pendientes ({len(approvals)}):")
        for a in approvals[:5]:
            lines.append(f"  [{a['risk']}] {a['action']} -> {a['target']}")

    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="HomeLab snapshot")
    p.add_argument("--status", action="store_true", help="Print + send to Telegram")
    p.add_argument("--json", action="store_true", help="Print JSON only")
    args = p.parse_args()

    if args.json:
        snap = snapshot()
        print(json.dumps(snap, indent=2, default=str, ensure_ascii=False))
    elif args.status:
        snap = snapshot()
        msg = format_telegram(snap)
        print(msg)
        _notify(msg)
    else:
        p.print_help()

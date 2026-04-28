#!/usr/bin/env python3
"""
Incident Clustering — Group similar incidents to detect hidden patterns.

Algorithm: cluster by (category + probable_cause + time_window_1h).
3+ incidents in same cluster → pattern detected.

Usage:
    python3 incident_clustering.py --analyze    # run clustering
    python3 incident_clustering.py --patterns   # show detected patterns
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from runtime_utils import now
try:
    from .runtime_utils import load_json
except ImportError:
    from runtime_utils import load_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
PATTERNS_FILE = STATE_DIR / "incident_patterns.json"
LOG_FILE = BASE_DIR / "logs" / "incident_clustering.log"
ERROR_MINING_DIR = STATE_DIR / "error_mining"
DIAGNOSTICS_LOG = BASE_DIR / "logs" / "diagnostics.log"

MIN_CLUSTER_SIZE = 3
TIME_WINDOW_HOURS = 1



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _load_jsonl(path: Path, max_age_hours: int = 168) -> list:
    entries = []
    cutoff = now() - timedelta(hours=max_age_hours)
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts_str = entry.get("ts", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str)
                        if ts >= cutoff:
                            entries.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return entries


def _cluster_key(incident: dict) -> str:
    """Generate cluster key from category + cause."""
    cat = incident.get("category", incident.get("type", "unknown"))
    cause = incident.get("probable_cause", incident.get("detail", ""))
    # Normalize: strip numbers, lowercase
    import re
    cause_norm = re.sub(r"\d+", "N", cause.lower())[:50]
    return f"{cat}:{cause_norm}"


def _hour_bucket(ts_str: str) -> str:
    """Extract hour bucket from timestamp."""
    try:
        dt = datetime.fromisoformat(ts_str)
        return f"{dt.hour:02d}:00-{(dt.hour+1) % 24:02d}:00"
    except (ValueError, TypeError):
        return "unknown"


# ── Main API ────────────────────────────────────────────────────


def collect_incidents(days: int = 7) -> list:
    """Collect incidents from error_mining reports and diagnostics logs."""
    incidents = []

    # From error_mining daily reports
    if ERROR_MINING_DIR.exists():
        for f in sorted(ERROR_MINING_DIR.glob("daily_*.json")):
            report = load_json(f, {})
            for error in report.get("errors", []):
                incidents.append({
                    "type": error.get("type", "unknown"),
                    "category": error.get("type", "unknown"),
                    "detail": error.get("detail", ""),
                    "probable_cause": error.get("subtype", error.get("detail", "")),
                    "ts": error.get("ts", ""),
                    "severity": error.get("severity", "info"),
                    "source": error.get("source", ""),
                })

    # From diagnostics log
    for entry in _load_jsonl(DIAGNOSTICS_LOG, max_age_hours=days * 24):
        for issue in entry.get("issues", []):
            incidents.append({
                "type": issue.get("rule", "diagnostic"),
                "category": issue.get("problem", "unknown")[:30],
                "detail": issue.get("problem", ""),
                "probable_cause": issue.get("cause", ""),
                "ts": entry.get("ts", ""),
                "severity": issue.get("severity", "info"),
                "source": "diagnostics",
            })

    return incidents


def cluster(incidents: list = None, days: int = 7) -> list:
    """Cluster incidents and detect patterns.

    Returns list of pattern dicts for clusters with 3+ incidents.
    """
    if incidents is None:
        incidents = collect_incidents(days)

    # Group by cluster key
    clusters = defaultdict(list)
    for inc in incidents:
        key = _cluster_key(inc)
        clusters[key].append(inc)

    # Build patterns from clusters >= MIN_CLUSTER_SIZE
    patterns = []
    for key, members in clusters.items():
        if len(members) < MIN_CLUSTER_SIZE:
            continue

        # Analyze timing
        hours = [_hour_bucket(m.get("ts", "")) for m in members]
        hour_counts = Counter(hours)
        peak_hour = hour_counts.most_common(1)[0][0] if hour_counts else "unknown"

        # Analyze duration (if timestamps available)
        timestamps = []
        for m in members:
            try:
                timestamps.append(datetime.fromisoformat(m["ts"]))
            except (ValueError, TypeError, KeyError):
                pass

        avg_duration_min = 0
        if len(timestamps) >= 2:
            timestamps.sort()
            gaps = [(timestamps[i+1] - timestamps[i]).total_seconds() / 60
                    for i in range(len(timestamps) - 1)]
            avg_duration_min = round(sum(gaps) / len(gaps), 1) if gaps else 0

        # Severity distribution
        severities = Counter(m.get("severity", "info") for m in members)

        cluster_id = sha256(key.encode()).hexdigest()[:12]

        patterns.append({
            "cluster_id": cluster_id,
            "pattern": key.split(":")[0],
            "detail": key,
            "occurrences": len(members),
            "peak_hour": peak_hour,
            "avg_gap_minutes": avg_duration_min,
            "severities": dict(severities),
            "first_seen": min((m.get("ts", "") for m in members), default=""),
            "last_seen": max((m.get("ts", "") for m in members), default=""),
            "probable_trigger": _guess_trigger(key, peak_hour, members),
            "recommendation": _suggest_fix(key, members),
        })

    # Sort by occurrences descending
    patterns.sort(key=lambda p: -p["occurrences"])

    # Save
    PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PATTERNS_FILE, "w") as f:
        json.dump(patterns, f, indent=2, default=str)

    _log({"action": "cluster_complete", "incidents": len(incidents),
          "patterns": len(patterns)})

    return patterns


def _guess_trigger(key: str, peak_hour: str, members: list) -> str:
    """Heuristic: guess what triggers this pattern."""
    if "backend_offline" in key or "cloud_fallback" in key:
        return "HomeLab goes to sleep or network drops"
    if "container" in key and "crash" in key:
        return "Memory pressure or dependency failure"
    if "vpn" in key:
        return "Gluetun connection reset by NordVPN"
    if "ollama" in key:
        return "HomeLab GPU under load or offline"
    if peak_hour.startswith("02") or peak_hour.startswith("03"):
        return "Backup window (2-4 AM) resource contention"
    return "Unknown — needs investigation"


def _suggest_fix(key: str, members: list) -> str:
    """Suggest a fix based on pattern."""
    if "backend_offline" in key:
        return "Configure HomeLab keepalive from NAS"
    if "container" in key and "crash" in key:
        return "Check container logs, add memory limits"
    if "vpn" in key:
        return "Switch NordVPN server, add gluetun health check"
    if "ollama" in key:
        return "Add Ollama health probe to runtime loop"
    if "repeated_incident" in key:
        return "Create automated runbook for this pattern"
    return "Investigate and create runbook"


def get_patterns() -> list:
    """Load cached patterns."""
    return load_json(PATTERNS_FILE, [])


def format_patterns(patterns: list) -> str:
    if not patterns:
        return "No patterns detected."
    lines = [f"Incident Patterns ({len(patterns)}):"]
    for p in patterns:
        lines.append(f"  [{p['occurrences']}x] {p['pattern']} — peak: {p['peak_hour']}")
        lines.append(f"    trigger: {p['probable_trigger']}")
        lines.append(f"    fix: {p['recommendation']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Incident Clustering")
    parser.add_argument("--analyze", action="store_true", help="Run clustering")
    parser.add_argument("--patterns", action="store_true", help="Show patterns")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.analyze:
        patterns = cluster()
        if args.json:
            print(json.dumps(patterns, indent=2, default=str))
        else:
            print(format_patterns(patterns))
    elif args.patterns:
        patterns = get_patterns()
        print(format_patterns(patterns))
    else:
        parser.print_help()

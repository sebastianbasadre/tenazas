#!/usr/bin/env python3
"""
Root Cause Engine — Build causal chains X → Y → Z for incidents.

Extends infra_diagnostics with causal reasoning. For each incident,
finds the root cause and suggests the correct fix target.

Usage:
    python3 root_cause_engine.py --analyze "ollama_slow"
    python3 root_cause_engine.py --chains    # list known chains
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "root_cause.log"

# ── Known causal chains ─────────────────────────────────────────
# Each chain: [root_cause, intermediate..., final_symptom]
# Fix should target the FIRST element, not the last.

CAUSAL_CHAINS = [
    {
        "id": "vram_cascade",
        "chain": ["vram_high", "ollama_slow", "classifier_timeout", "cloud_fallback"],
        "fix_action": "gpu_scheduler --optimize",
        "description": "VRAM pressure slows Ollama → classifiers time out → fall to cloud",
    },
    {
        "id": "ram_cascade",
        "chain": ["ram_high", "container_oom", "container_crash", "service_down"],
        "fix_action": "identify memory-heavy container and restart",
        "description": "NAS RAM exhaustion → OOM killer → container crash",
    },
    {
        "id": "vpn_cascade",
        "chain": ["vpn_leak", "arr_exposed", "qbittorrent_blocked", "downloads_stopped"],
        "fix_action": "restart gluetun",
        "description": "VPN leak exposes real IP → ISP blocks torrents",
    },
    {
        "id": "disk_cascade",
        "chain": ["disk_full", "log_write_fail", "container_crash", "data_loss_risk"],
        "fix_action": "cleanup logs and old media",
        "description": "Disk full → log writes fail → containers crash",
    },
    {
        "id": "network_cascade",
        "chain": ["network_down", "ollama_unreachable", "all_local_backends_offline",
                  "cloud_fallback_storm"],
        "fix_action": "diagnose network connectivity",
        "description": "Network down → all local backends unreachable → mass cloud fallback",
    },
    {
        "id": "homelab_offline",
        "chain": ["homelab_offline", "ollama_unreachable", "cloud_fallback"],
        "fix_action": "check HomeLab power/network",
        "description": "HomeLab offline → Ollama unreachable → cloud fallback",
    },
    {
        "id": "gluetun_cascade",
        "chain": ["gluetun_down", "vpn_down", "arr_stack_offline"],
        "fix_action": "restart gluetun",
        "description": "Gluetun down → VPN down → arr-stack loses network",
    },
]

# Reverse index: symptom → chain(s) that can cause it
_SYMPTOM_INDEX = {}
for chain_def in CAUSAL_CHAINS:
    for i, node in enumerate(chain_def["chain"]):
        if node not in _SYMPTOM_INDEX:
            _SYMPTOM_INDEX[node] = []
        _SYMPTOM_INDEX[node].append({
            "chain_id": chain_def["id"],
            "position": i,
            "is_root": i == 0,
            "root": chain_def["chain"][0],
            "chain": chain_def["chain"],
            "fix_action": chain_def["fix_action"],
        })



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


# ── Main API ────────────────────────────────────────────────────


def analyze(incident_type: str, evidence: dict = None) -> dict:
    """Analyze an incident and find its root cause.

    Args:
        incident_type: The observed symptom (e.g. "cloud_fallback", "container_crash")
        evidence: Optional dict with additional context (metrics, timestamps)

    Returns:
        {incident_type, root_cause, chain, fix_target, fix_action, confidence, is_symptom}
    """
    matches = _SYMPTOM_INDEX.get(incident_type, [])

    if not matches:
        _log({"action": "analyze", "incident": incident_type, "result": "no_chain_found"})
        return {
            "incident_type": incident_type,
            "root_cause": "unknown",
            "chain": [incident_type],
            "fix_target": incident_type,
            "fix_action": "investigate manually",
            "confidence": 0.3,
            "is_symptom": False,
        }

    # Find the best match (deepest in chain = most likely a symptom)
    best = max(matches, key=lambda m: m["position"])
    is_root = best["is_root"]

    # Calculate confidence based on position in chain
    # Root cause → high confidence, late symptom → lower
    position_factor = 1.0 - (best["position"] / len(best["chain"])) * 0.3
    confidence = round(min(position_factor * 0.9, 0.95), 2)

    # If evidence provides additional context, boost/reduce confidence
    if evidence:
        if evidence.get("confirmed"):
            confidence = min(confidence + 0.1, 0.95)

    # Build the causal sub-chain from root to this symptom
    chain_up_to = best["chain"][:best["position"] + 1]

    result = {
        "incident_type": incident_type,
        "root_cause": best["root"],
        "chain": chain_up_to,
        "fix_target": best["root"],
        "fix_action": best["fix_action"],
        "confidence": confidence,
        "is_symptom": not is_root,
        "chain_id": best["chain_id"],
    }

    _log({"action": "analyze", "incident": incident_type,
          "root_cause": result["root_cause"], "confidence": confidence,
          "chain_id": best["chain_id"]})

    return result


def analyze_multiple(incidents: list) -> list:
    """Analyze multiple incidents and return prioritized fixes.

    Deduplicates by root cause — if multiple symptoms share a root,
    only suggests fixing the root once.
    """
    results = []
    seen_roots = set()

    for inc in incidents:
        inc_type = inc if isinstance(inc, str) else inc.get("type", inc.get("category", ""))
        evidence = inc if isinstance(inc, dict) else None
        analysis = analyze(inc_type, evidence)

        if analysis["root_cause"] not in seen_roots:
            results.append(analysis)
            seen_roots.add(analysis["root_cause"])

    # Sort by confidence descending
    results.sort(key=lambda r: -r["confidence"])

    _log({"action": "analyze_multiple", "incidents": len(incidents),
          "unique_roots": len(results)})
    return results


def get_fix_for(incident_type: str) -> str | None:
    """Quick lookup: what's the correct fix for this incident type?"""
    result = analyze(incident_type)
    if result["root_cause"] == "unknown":
        return None
    return result["fix_action"]


def is_symptom(incident_type: str) -> bool:
    """Check if this incident is a symptom of a deeper cause."""
    result = analyze(incident_type)
    return result["is_symptom"]


def list_chains() -> list:
    """List all known causal chains."""
    return [{"id": c["id"], "chain": c["chain"],
             "fix": c["fix_action"], "description": c["description"]}
            for c in CAUSAL_CHAINS]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Root Cause Engine")
    p.add_argument("--analyze", type=str, help="Analyze incident type")
    p.add_argument("--chains", action="store_true", help="List known chains")
    args = p.parse_args()

    if args.analyze:
        result = analyze(args.analyze)
        print(json.dumps(result, indent=2))
    elif args.chains:
        for c in list_chains():
            print(f"  {c['id']}: {' → '.join(c['chain'])}")
            print(f"    fix: {c['fix']}")
    else:
        p.print_help()

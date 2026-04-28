#!/usr/bin/env python3
"""
Network Cache — Caches expensive nmap scans with configurable TTL.

Wraps nmap calls to avoid running scans more than once every 30 minutes.
Does NOT modify existing network scan logic — just adds a cache layer.

Usage:
    from runtime.network_cache import cached_nmap_scan
    hosts = cached_nmap_scan(LAN_SUBNET)
"""

import json
import subprocess
import time
from pathlib import Path

try:
    from runtime_config import LAN_SUBNET
except ImportError:
    from workspace.runtime.runtime_config import LAN_SUBNET

BASE_DIR = Path(__file__).parent
CACHE_FILE = BASE_DIR / "nmap_cache.json"
CACHE_TTL = 1800  # 30 minutes
SCAN_TIMEOUT = 30  # seconds


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(data: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_cache_valid(subnet: str = LAN_SUBNET) -> bool:
    """Check if cached scan result is still fresh."""
    cache = _load_cache()
    entry = cache.get(subnet, {})
    ts = entry.get("ts", 0)
    return (time.time() - ts) < CACHE_TTL


def cached_nmap_scan(subnet: str = LAN_SUBNET) -> dict:
    """Run nmap scan or return cached result.

    Returns:
        {"hosts": int, "ts": float, "cached": bool, "raw": str}
    """
    cache = _load_cache()
    entry = cache.get(subnet, {})

    if entry and (time.time() - entry.get("ts", 0)) < CACHE_TTL:
        return {**entry, "cached": True}

    # Run actual scan
    try:
        result = subprocess.run(
            ["nmap", "-sn", subnet],
            capture_output=True, text=True, timeout=SCAN_TIMEOUT,
        )
        if result.returncode == 0:
            # Count hosts from nmap output
            lines = result.stdout.strip().split("\n")
            host_lines = [l for l in lines if "Nmap scan report" in l]
            entry = {
                "hosts": len(host_lines),
                "ts": time.time(),
                "raw": result.stdout[-500:],  # Keep last 500 chars
            }
            cache[subnet] = entry
            _save_cache(cache)
            return {**entry, "cached": False}
    except subprocess.TimeoutExpired:
        pass
    except FileNotFoundError:
        pass

    # Return stale cache if scan failed
    if entry:
        return {**entry, "cached": True, "stale": True}
    return {"hosts": 0, "ts": 0, "cached": False, "error": "scan failed"}


def invalidate(subnet: str = LAN_SUBNET):
    """Force next call to run a fresh scan."""
    cache = _load_cache()
    cache.pop(subnet, None)
    _save_cache(cache)


def get_cache_age(subnet: str = LAN_SUBNET) -> float:
    """Return seconds since last scan, or -1 if no cache."""
    cache = _load_cache()
    entry = cache.get(subnet, {})
    ts = entry.get("ts", 0)
    return time.time() - ts if ts > 0 else -1

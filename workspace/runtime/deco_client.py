#!/usr/bin/env python3
"""
TP-Link Deco Client — Query Deco B63 mesh for connected clients.

Uses tplinkrouterc6u.TPLinkDecoClient to talk to the Deco local API.
Extracts: hostname, MAC, IP, band (2.4/5GHz/IoT), connection type, speed.

Credentials read from openclaw.json → tenazas.deco_password (or env DECO_PASSWORD).
Deco main unit IP defaults to 192.168.2.30.

Writes results to runtime/deco_clients.json for network_exporter.py.

Usage:
    python3 deco_client.py                # Fetch + save + print table
    python3 deco_client.py --json         # JSON output
    python3 deco_client.py --save-only    # Fetch + save, no output
    python3 deco_client.py --status       # Router status (CPU, mem, WAN)
    python3 deco_client.py --cached       # Show cached data
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger("deco_client")

BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "deco_clients.json"
LOG_FILE = BASE_DIR / "logs" / "deco_client.log"

# Deco config
DECO_IP = os.environ.get("DECO_IP", "192.168.2.30")
DECO_PASSWORD = os.environ.get("DECO_PASSWORD", "")

# Try to load password from openclaw.json
if not DECO_PASSWORD:
    try:
        config_paths = [
            Path("/home/tenazas/.openclaw/openclaw.json"),
            BASE_DIR.parent / "state" / "openclaw.json",
        ]
        for cfg_path in config_paths:
            if cfg_path.exists():
                with open(cfg_path) as f:
                    cfg = json.load(f)
                DECO_PASSWORD = (
                    cfg.get("tenazas", {}).get("deco_password", "") or
                    cfg.get("deco_password", "")
                )
                if DECO_PASSWORD:
                    break
    except Exception:
        pass

# Connection type mapping from tplinkrouterc6u enums
BAND_MAP = {
    "host_2g": ("2.4GHz", "wifi"),
    "host_5g": ("5GHz", "wifi"),
    "host_6g": ("6GHz", "wifi"),
    "iot_2g": ("2.4GHz", "wifi_iot"),
    "iot_5g": ("5GHz", "wifi_iot"),
    "guest_2g": ("2.4GHz", "wifi_guest"),
    "guest_5g": ("5GHz", "wifi_guest"),
    "wired": ("-", "ethernet"),
}


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps({**entry, "ts": _now()}, default=str) + "\n")
    except OSError:
        pass


def fetch_clients() -> list:
    """Fetch connected clients from Deco mesh.

    Returns list of dicts:
        {ip, name, mac, band, connection_type, active, down_speed, up_speed}
    """
    try:
        from tplinkrouterc6u import TPLinkDecoClient
    except ImportError:
        _log({"action": "import_error", "error": "tplinkrouterc6u not installed"})
        return []

    if not DECO_PASSWORD:
        _log({"action": "no_password", "error": "DECO_PASSWORD not set"})
        return []

    clients = []
    try:
        router = TPLinkDecoClient(DECO_IP, DECO_PASSWORD)
        router.authorize()

        status = router.get_status()

        if status and hasattr(status, "devices") and status.devices:
            for dev in status.devices:
                # Parse connection type
                conn_str = str(getattr(dev, "type", "unknown"))
                # Extract the value from enum like "Connection.HOST_5G: 'host_5g'"
                if "'" in conn_str:
                    conn_str = conn_str.split("'")[1]
                elif "." in conn_str:
                    conn_str = conn_str.split(".")[-1].lower()

                band, conn_type = BAND_MAP.get(conn_str, ("unknown", "unknown"))

                entry = {
                    "ip": str(getattr(dev, "ipaddr", "")),
                    "name": str(getattr(dev, "hostname", "")),
                    "mac": str(getattr(dev, "macaddr", "")).replace("-", ":"),
                    "band": band,
                    "connection_type": conn_type,
                    "active": bool(getattr(dev, "active", False)),
                    "down_speed": getattr(dev, "down_speed", 0) or 0,
                    "up_speed": getattr(dev, "up_speed", 0) or 0,
                }

                clients.append(entry)

        router.logout()
        _log({"action": "fetch_ok", "clients": len(clients),
              "wifi": status.wifi_clients_total if status else 0,
              "wired": status.wired_total if status else 0})

    except Exception as e:
        _log({"action": "fetch_error", "error": str(e)[:200]})

    return clients


def fetch_status() -> dict:
    """Fetch router status (CPU, mem, WAN, client counts)."""
    try:
        from tplinkrouterc6u import TPLinkDecoClient
    except ImportError:
        return {"error": "tplinkrouterc6u not installed"}

    if not DECO_PASSWORD:
        return {"error": "DECO_PASSWORD not set"}

    try:
        router = TPLinkDecoClient(DECO_IP, DECO_PASSWORD)
        router.authorize()
        status = router.get_status()
        result = {
            "cpu_usage": status.cpu_usage,
            "mem_usage": status.mem_usage,
            "wan_ip": str(status.wan_ipv4_addr) if status.wan_ipv4_addr else None,
            "lan_ip": str(status.lan_ipv4_addr) if status.lan_ipv4_addr else None,
            "wifi_clients": status.wifi_clients_total,
            "wired_clients": status.wired_total,
            "total_clients": status.clients_total,
            "wifi_2g": status.wifi_2g_enable,
            "wifi_5g": status.wifi_5g_enable,
        }
        router.logout()
        return result
    except Exception as e:
        return {"error": str(e)[:200]}


def save_clients(clients: list):
    """Save client list to deco_clients.json."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(clients, f, indent=2, default=str)


def get_clients() -> list:
    """Load cached client list."""
    try:
        with open(OUTPUT_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def format_table(clients: list) -> str:
    """Format clients as table."""
    if not clients:
        return "No Deco client data."

    lines = []
    lines.append(
        "%-18s %-24s %-8s %-12s %-18s %s" %
        ("IP", "Name", "Band", "Type", "MAC", "Active")
    )
    lines.append("-" * 90)

    def sort_key(c):
        try:
            return tuple(int(p) for p in c.get("ip", "0.0.0.0").split("."))
        except ValueError:
            return (999,)

    for c in sorted(clients, key=sort_key):
        ip = c.get("ip", "?")
        name = c.get("name", "-")[:23]
        band = c.get("band", "?")
        conn = c.get("connection_type", "?")[:11]
        mac = c.get("mac", "-")
        active = "yes" if c.get("active") else "no"
        lines.append("%-18s %-24s %-8s %-12s %-18s %s" % (ip, name, band, conn, mac, active))

    # Summary
    wifi_24 = sum(1 for c in clients if "2.4" in c.get("band", ""))
    wifi_5 = sum(1 for c in clients if c.get("band") == "5GHz")
    eth = sum(1 for c in clients if c.get("connection_type") == "ethernet")
    active = sum(1 for c in clients if c.get("active"))
    lines.append("")
    lines.append("Total: %d  Active: %d  WiFi 2.4GHz: %d  WiFi 5GHz: %d  Ethernet: %d" %
                 (len(clients), active, wifi_24, wifi_5, eth))

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TP-Link Deco Client")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--save-only", action="store_true", help="Fetch + save, no output")
    parser.add_argument("--cached", action="store_true", help="Show cached data only")
    parser.add_argument("--status", action="store_true", help="Show router status")
    args = parser.parse_args()

    if args.status:
        result = fetch_status()
        print(json.dumps(result, indent=2, default=str))
        return

    if args.cached:
        clients = get_clients()
    else:
        clients = fetch_clients()
        if clients:
            save_clients(clients)

    if args.save_only:
        return
    elif args.json:
        print(json.dumps(clients, indent=2, default=str))
    else:
        print(format_table(clients))


if __name__ == "__main__":
    main()

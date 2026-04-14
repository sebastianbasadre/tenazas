#!/usr/bin/env python3
"""
TP-Link Deco Client — Query Deco mesh for connected clients.

Uses tplinkrouterc6u library to talk to the Deco B63 local API.
Extracts: hostname, MAC, IP, band (2.4/5GHz), connection type, signal.

Credentials read from openclaw.json → tenazas.deco_password (or env DECO_PASSWORD).
Deco IP defaults to 192.168.2.12 (main unit).

Writes results to runtime/deco_clients.json for network_exporter.py.

Usage:
    python3 deco_client.py                # Fetch + save + print table
    python3 deco_client.py --json         # JSON output
    python3 deco_client.py --save-only    # Fetch + save, no output
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
        {ip, name, mac, band, connection_type, signal, packets_sent, packets_recv}
    """
    try:
        from tplinkrouterc6u import TplinkRouterProvider, TplinkRouter
        from tplinkrouterc6u.enum.connection import Wifi
    except ImportError:
        _log({"action": "import_error", "error": "tplinkrouterc6u not installed"})
        return []

    if not DECO_PASSWORD:
        _log({"action": "no_password", "error": "DECO_PASSWORD not set"})
        return []

    clients = []
    try:
        router = TplinkRouter(DECO_IP, DECO_PASSWORD)
        router.authorize()

        firmware = router.get_firmware()
        status = router.get_status()

        if status and hasattr(status, "clients"):
            for mac_addr, client in status.clients.items():
                # Determine band
                band = "unknown"
                conn_type = "ethernet"
                if hasattr(client, 'type') and client.type:
                    ct = str(client.type)
                    if '5' in ct:
                        band = "5GHz"
                        conn_type = "wifi_5g"
                    elif '2' in ct or 'wifi' in ct.lower():
                        band = "2.4GHz"
                        conn_type = "wifi_2.4g"
                    elif 'wire' in ct.lower() or 'eth' in ct.lower():
                        conn_type = "ethernet"
                        band = "-"

                entry = {
                    "ip": str(client.ipaddress) if hasattr(client, 'ipaddress') else "",
                    "name": str(client.name) if hasattr(client, 'name') else "",
                    "mac": str(mac_addr),
                    "band": band,
                    "connection_type": conn_type,
                    "signal": 0,
                }

                # Signal if available
                if hasattr(client, 'signal') and client.signal:
                    try:
                        entry["signal"] = int(client.signal)
                    except (ValueError, TypeError):
                        pass

                # Packets if available
                if hasattr(client, 'packets_sent'):
                    entry["packets_sent"] = client.packets_sent
                if hasattr(client, 'packets_recv'):
                    entry["packets_recv"] = client.packets_recv

                clients.append(entry)

        router.logout()
        _log({"action": "fetch_ok", "clients": len(clients)})

    except Exception as e:
        _log({"action": "fetch_error", "error": str(e)[:200]})
        # Try alternative approach with raw API
        try:
            clients = _fetch_raw(DECO_IP, DECO_PASSWORD)
        except Exception as e2:
            _log({"action": "fetch_raw_error", "error": str(e2)[:200]})

    return clients


def _fetch_raw(ip: str, password: str) -> list:
    """Fallback: direct HTTP to Deco API if tplinkrouterc6u fails."""
    # This is a placeholder for raw API access
    # The tplinkrouterc6u library handles the crypto handshake
    return []


def save_clients(clients: list):
    """Save client list to deco_clients.json."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "ts": _now(),
        "total": len(clients),
        "clients": clients,
    }
    # Save flat list for network_exporter compatibility
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
        return "No Deco client data available."

    lines = []
    lines.append(f"{'IP':<18} {'Name':<22} {'Band':<8} {'Type':<12} {'MAC':<18}")
    lines.append("-" * 80)

    for c in sorted(clients, key=lambda x: tuple(int(p) for p in x.get("ip", "0.0.0.0").split(".") if p.isdigit())):
        ip = c.get("ip", "?")
        name = c.get("name", "-")[:21]
        band = c.get("band", "?")
        conn = c.get("connection_type", "?")[:11]
        mac = c.get("mac", "-")
        lines.append(f"{ip:<18} {name:<22} {band:<8} {conn:<12} {mac:<18}")

    # Summary
    wifi_24 = sum(1 for c in clients if "2.4" in c.get("band", ""))
    wifi_5 = sum(1 for c in clients if "5" in c.get("band", ""))
    eth = sum(1 for c in clients if "ethernet" in c.get("connection_type", ""))
    lines.append("")
    lines.append(f"Total: {len(clients)} — WiFi 2.4GHz: {wifi_24}, WiFi 5GHz: {wifi_5}, Ethernet: {eth}")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TP-Link Deco Client")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--save-only", action="store_true", help="Fetch + save, no output")
    parser.add_argument("--cached", action="store_true", help="Show cached data only")
    args = parser.parse_args()

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

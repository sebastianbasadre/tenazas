#!/usr/bin/env python3
"""
Device Discovery — Network scanner and device inventory for Tenazas.

Scans the LAN using nmap with multiple techniques:
- Quick scan (-sn): host discovery only (fast, ~10s)
- Deep scan (-A): OS, services, MAC vendor, ports (slow, ~5 min)
- Targeted scan (-sV -O): specific IP deep probe

Builds and maintains a persistent device inventory JSON file.
Integrates with world_model.py for live topology data.

Usage:
    # Quick scan (host discovery)
    python3 device_discovery.py --quick

    # Deep scan (full fingerprint)
    python3 device_discovery.py --deep

    # Scan specific IP
    python3 device_discovery.py --target 192.168.2.22

    # Show current inventory
    python3 device_discovery.py --inventory

    # Show inventory as table
    python3 device_discovery.py --table

    # Export for Telegram
    python3 device_discovery.py --telegram
"""

import json
import re
import subprocess
import sys
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path

BASE_DIR = Path(__file__).parent
INVENTORY_FILE = BASE_DIR / "device_inventory.json"
LOG_FILE = BASE_DIR / "logs" / "device_discovery.log"
SUBNET = "192.168.2.0/24"

# Timeouts
QUICK_SCAN_TIMEOUT = 30
DEEP_SCAN_TIMEOUT = 600  # 10 min max for full -A scan
TARGET_SCAN_TIMEOUT = 60

# ── Known device overrides (manual enrichment) ──────────────────

KNOWN_OVERRIDES = {
    "192.168.2.1": {"name": "Router", "category": "network", "control": None},
    "192.168.2.2": {"name": "NAS DS923+", "category": "server", "control": "ssh"},
    "192.168.2.3": {"name": "NAS Backup", "category": "server", "control": "ssh"},
    "192.168.2.6": {"name": "HomeLab", "category": "server", "control": "ssh"},
    "192.168.2.11": {"name": "Hue Bridge", "category": "smart_home",
                      "control": "http", "api": "http://192.168.2.11/api"},
    "192.168.2.46": {"name": "Samsung M8", "category": "display",
                      "control": "websocket", "api": "ws://192.168.2.46:8001"},
    "192.168.2.30": {"name": "TP-Link Deco B63 main", "category": "network", "control": "http"},
    "192.168.2.12": {"name": "TP-Link Deco extender 1", "category": "network", "control": "http"},
    "192.168.2.23": {"name": "TP-Link Deco extender 2", "category": "network", "control": "http"},
    "192.168.2.62": {"name": "Google Nest Mini", "category": "speaker", "control": "cast"},
    "192.168.2.57": {"name": "Moto G75 Sebas", "category": "phone", "control": None},
    # Cameras (EZVIZ)
    "192.168.2.10": {"name": "EZVIZ C6N (2)", "category": "camera", "control": "http"},
    "192.168.2.16": {"name": "EZVIZ H3c (2)", "category": "camera", "control": "http"},
    "192.168.2.17": {"name": "EZVIZ C6N (3)", "category": "camera", "control": "http"},
    "192.168.2.18": {"name": "EZVIZ H1c", "category": "camera", "control": "http"},
    "192.168.2.19": {"name": "EZVIZ H1c (2)", "category": "camera", "control": "http"},
    "192.168.2.25": {"name": "EZVIZ H9c", "category": "camera", "control": "http"},
    "192.168.2.27": {"name": "EZVIZ H3c", "category": "camera", "control": "http"},
    "192.168.2.68": {"name": "EZVIZ BE3729184", "category": "camera", "control": "http"},
    "192.168.2.138": {"name": "EZVIZ C6N", "category": "camera", "control": "http"},
    # NVR
    "192.168.2.4": {"name": "Dahua NVR", "category": "camera", "control": "http"},
    # Media
    "192.168.2.80": {"name": "Marantz CINEMA 60", "category": "media", "control": "http"},
    "192.168.2.36": {"name": "Hue Sync Box", "category": "smart_home", "control": "http"},
    "192.168.2.83": {"name": "Google Home Mini", "category": "speaker", "control": "cast"},
    "192.168.2.22": {"name": "Nest Doorbell Wired", "category": "smart_home", "control": None},
    # IoT
    "192.168.2.99": {"name": "Xiaomi Vacuum", "category": "iot", "control": None},
    "192.168.2.61": {"name": "ESP32", "category": "iot", "control": None},
    "192.168.2.5": {"name": "Epson Printer", "category": "printer", "control": "http"},
    # Phones & tablets
    "192.168.2.14": {"name": "Tab S11 Sebas", "category": "tablet", "control": None},
    "192.168.2.100": {"name": "S25 Ultra Sebas", "category": "phone", "control": None},
    "192.168.2.102": {"name": "S25 Viviana", "category": "phone", "control": None},
    "192.168.2.128": {"name": "Tab A9 Sebas", "category": "tablet", "control": None},
    "192.168.2.60": {"name": "Tab S6 Lite Juliana", "category": "tablet", "control": None},
    "192.168.2.29": {"name": "Samsung TV", "category": "display", "control": None},
    "192.168.2.32": {"name": "Tab A9 Viviana", "category": "tablet", "control": None},
    "192.168.2.35": {"name": "Android (ethernet)", "category": "client", "control": None},
    "192.168.2.56": {"name": "Phone (5GHz)", "category": "phone", "control": None},
}

# ── Device classification rules ─────────────────────────────────

VENDOR_CATEGORIES = {
    "philips": "smart_home",
    "signify": "smart_home",  # Philips Hue parent company
    "samsung": "display",
    "synology": "server",
    "espressif": "iot",       # ESP32/ESP8266
    "tuya": "iot",
    "sonos": "media",
    "apple": "client",
    "google": "media",
    "amazon": "media",
    "intel": "client",
    "realtek": "client",
    "tp-link": "network",
    "ubiquiti": "network",
    "netgear": "network",
    "asus": "network",
    "raspberry": "server",
    "broadcom": "iot",
}

PORT_CATEGORIES = {
    80: "http",
    443: "https",
    22: "ssh",
    8080: "http_alt",
    8443: "https_alt",
    554: "rtsp",       # cameras
    1883: "mqtt",       # IoT
    8883: "mqtt_tls",
    5353: "mdns",
    1900: "upnp",
    9090: "prometheus",
    3000: "grafana",
    8100: "chromadb",
    11434: "ollama",
    8888: "proxy",
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


def _load_inventory() -> dict:
    """Load existing inventory. Returns {ip: device_dict}."""
    try:
        with open(INVENTORY_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            # Convert list format to dict keyed by IP
            return {d["ip"]: d for d in data if "ip" in d}
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_inventory(inventory: dict):
    INVENTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Save as list sorted by IP for readability
    devices = sorted(inventory.values(), key=lambda d: _ip_sort_key(d.get("ip", "")))
    with open(INVENTORY_FILE, "w") as f:
        json.dump(devices, f, indent=2, default=str)


def _ip_sort_key(ip: str) -> tuple:
    """Sort IPs numerically."""
    try:
        return tuple(int(p) for p in ip.split("."))
    except (ValueError, AttributeError):
        return (999, 999, 999, 999)


# ── OUI vendor lookup (common prefixes) ─────────────────────────

OUI_DB = {
    "00:17:88": "Philips Hue",
    "EC:B5:FA": "Philips Hue",
    "00:11:32": "Synology",
    "00:1A:2B": "Ayecom",
    "00:50:56": "VMware",
    "02:42": "Docker",
    "E4:FA:C4": "Samsung",
    "D0:03:4B": "Samsung",
    "8C:49:62": "Samsung",
    "FC:A1:83": "Samsung",
    "B4:6B:FC": "Samsung",
    "20:1C:3A": "Apple",
    "20:1F:3B": "Apple",
    "3C:22:FB": "Apple",
    "A4:83:E7": "Apple",
    "F0:18:98": "Apple",
    "AC:DE:48": "Apple",
    "70:C9:32": "Intel",
    "38:2C:E5": "Intel",
    "A0:36:9F": "Intel",
    "F8:B4:6A": "Hewlett Packard",
    "B0:5C:DA": "HP",
    "E8:6A:64": "TP-Link",
    "50:C7:BF": "TP-Link",
    "30:DE:4B": "TP-Link",
    "18:D6:C7": "TP-Link",
    "24:5A:4C": "Ubiquiti",
    "78:8A:20": "Ubiquiti",
    "B4:FB:E4": "Ubiquiti",
    "DC:A6:32": "Raspberry Pi",
    "E4:5F:01": "Raspberry Pi",
    "28:CD:C1": "Raspberry Pi",
    "2C:CF:67": "Espressif (ESP32)",
    "24:0A:C4": "Espressif (ESP32)",
    "A4:CF:12": "Espressif (ESP32)",
    "30:AE:A4": "Espressif (ESP32)",
    "84:CC:A8": "Espressif (ESP32)",
    "08:3A:F2": "Espressif (ESP32)",
    "FA:05:CF": "Tuya",
    "D8:F1:5B": "Tuya",
    "7C:F6:66": "Tuya",
    "48:55:19": "Google",
    "F4:F5:D8": "Google",
    "54:60:09": "Google",
    "A4:77:33": "Google Nest",
    "F8:0F:F9": "Google",
    "44:07:0B": "Google",
    "68:A8:6D": "Sonos",
    "B8:E9:37": "Sonos",
    "00:0E:58": "Sonos",
    "F0:F6:1C": "Amazon",
    "FC:65:DE": "Amazon",
    "34:D2:70": "Amazon Echo",
    "68:54:FD": "Amazon",
    "74:C6:3B": "Amazon",
    "F0:27:2D": "Amazon",
    "A0:02:DC": "Amazon",
    "38:F7:3D": "Amazon",
    "04:D3:B0": "Intel",
    "00:1E:67": "Intel",
    "64:6E:69": "Intel",
    "5C:87:9C": "Intel",
}


def _lookup_oui(mac: str) -> str:
    """Lookup vendor by MAC OUI prefix."""
    mac_upper = mac.upper()
    # Try 3-byte prefix first (XX:XX:XX)
    prefix3 = mac_upper[:8]
    if prefix3 in OUI_DB:
        return OUI_DB[prefix3]
    # Try 2-byte prefix (XX:XX) for Docker etc
    prefix2 = mac_upper[:5]
    if prefix2 in OUI_DB:
        return OUI_DB[prefix2]
    return ""


def _read_arp_table() -> dict:
    """Read /proc/net/arp for MAC addresses. Returns {ip: mac}."""
    arp = {}
    try:
        with open("/proc/net/arp") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[0] != "IP":
                    ip = parts[0]
                    mac = parts[3].upper()
                    # Skip incomplete entries and Docker internal
                    if mac != "00:00:00:00:00:00" and ip.startswith("192.168."):
                        arp[ip] = mac
    except (FileNotFoundError, PermissionError):
        pass
    return arp


def _enrich_with_arp(devices: list) -> list:
    """Add MAC and vendor from ARP table to devices missing them."""
    arp_table = _read_arp_table()
    for device in devices:
        ip = device.get("ip", "")
        if not device.get("mac") and ip in arp_table:
            device["mac"] = arp_table[ip]
        if device.get("mac") and not device.get("vendor"):
            vendor = _lookup_oui(device["mac"])
            if vendor:
                device["vendor"] = vendor
    return devices


# ── nmap parsers ────────────────────────────────────────────────

def _run_nmap(args: list, timeout: int) -> str:
    """Run nmap with args, return stdout."""
    cmd = ["nmap"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            return result.stdout
        _log({"action": "nmap_error", "args": args, "stderr": result.stderr[:500]})
        return result.stdout  # may still have partial output
    except subprocess.TimeoutExpired:
        _log({"action": "nmap_timeout", "args": args, "timeout": timeout})
        return ""
    except FileNotFoundError:
        _log({"action": "nmap_not_found"})
        return ""


def parse_quick_scan(output: str) -> list:
    """Parse nmap -sn output. Returns list of {ip, mac, vendor}."""
    devices = []
    current = {}

    for line in output.split("\n"):
        # "Nmap scan report for 192.168.2.1" or "Nmap scan report for hostname (192.168.2.1)"
        m = re.match(r"Nmap scan report for (?:(\S+) \()?(\d+\.\d+\.\d+\.\d+)\)?", line)
        if not m:
            m = re.match(r"Nmap scan report for (\d+\.\d+\.\d+\.\d+)", line)
        if m:
            if current.get("ip"):
                devices.append(current)
            groups = m.groups()
            if len(groups) == 2 and groups[0]:
                current = {"ip": groups[1], "hostname": groups[0]}
            else:
                ip = groups[-1] if groups[-1] else groups[0]
                current = {"ip": ip}
            continue

        # "MAC Address: AA:BB:CC:DD:EE:FF (Vendor Name)"
        m = re.match(r"MAC Address: ([0-9A-Fa-f:]+)\s*\(?(.*?)\)?$", line)
        if m and current:
            current["mac"] = m.group(1).upper()
            vendor = m.group(2).strip().rstrip(")")
            if vendor:
                current["vendor"] = vendor
            continue

    if current.get("ip"):
        devices.append(current)

    return devices


def parse_deep_scan(output: str) -> list:
    """Parse nmap -A output. Returns list of enriched device dicts."""
    devices = []
    current = {}
    in_ports = False

    for line in output.split("\n"):
        # New host
        m = re.match(r"Nmap scan report for (?:(\S+) \()?(\d+\.\d+\.\d+\.\d+)\)?", line)
        if not m:
            m = re.match(r"Nmap scan report for (\d+\.\d+\.\d+\.\d+)", line)
        if m:
            if current.get("ip"):
                devices.append(current)
            groups = m.groups()
            if len(groups) == 2 and groups[0]:
                current = {"ip": groups[1], "hostname": groups[0], "ports": [], "services": []}
            else:
                ip = groups[-1] if groups[-1] else groups[0]
                current = {"ip": ip, "ports": [], "services": []}
            in_ports = False
            continue

        # MAC
        m = re.match(r"MAC Address: ([0-9A-Fa-f:]+)\s*\(?(.*?)\)?$", line)
        if m and current:
            current["mac"] = m.group(1).upper()
            vendor = m.group(2).strip().rstrip(")")
            if vendor:
                current["vendor"] = vendor
            continue

        # Port line: "80/tcp open http Apache httpd 2.4.54"
        m = re.match(r"(\d+)/(tcp|udp)\s+(\w+)\s+(.*)", line)
        if m and current:
            port_num = int(m.group(1))
            state = m.group(3)
            service_info = m.group(4).strip()
            if state == "open":
                current["ports"].append(port_num)
                current["services"].append({
                    "port": port_num,
                    "proto": m.group(2),
                    "service": service_info.split()[0] if service_info else "unknown",
                    "detail": service_info,
                })
            continue

        # OS detection: "OS details: Linux 4.15 - 5.8"
        m = re.match(r"OS details:\s*(.*)", line)
        if m and current:
            current["os"] = m.group(1).strip()
            continue

        # Aggressive OS: "Running: Linux 4.X|5.X"
        m = re.match(r"Running:\s*(.*)", line)
        if m and current and "os" not in current:
            current["os_guess"] = m.group(1).strip()
            continue

        # Device type: "Device type: general purpose|WAP|printer"
        m = re.match(r"Device type:\s*(.*)", line)
        if m and current:
            current["device_type_nmap"] = m.group(1).strip()
            continue

    if current.get("ip"):
        devices.append(current)

    return devices


# ── Classification ──────────────────────────────────────────────

def classify_device(device: dict) -> dict:
    """Add category and control method based on vendor, ports, OS."""
    ip = device.get("ip", "")

    # Manual override first
    if ip in KNOWN_OVERRIDES:
        override = KNOWN_OVERRIDES[ip]
        device.update({k: v for k, v in override.items() if v is not None})
        if override.get("control") is None and "control" in device:
            pass  # keep existing
        return device

    # Classify by vendor (overrides "unknown" category)
    vendor = device.get("vendor", "").lower()
    for key, cat in VENDOR_CATEGORIES.items():
        if key in vendor:
            if device.get("category", "unknown") == "unknown":
                device["category"] = cat
            break

    # Classify by ports (only if still unknown)
    ports = device.get("ports", [])
    if device.get("category", "unknown") == "unknown" and ports:
        if 554 in ports or 8554 in ports:
            device["category"] = "camera"
        elif 1883 in ports or 8883 in ports:
            device["category"] = "iot"
        elif 9100 in ports or 631 in ports:
            device["category"] = "printer"
        elif 80 in ports or 443 in ports:
            device["category"] = "web_device"

    # Classify by OS (only if still unknown)
    if device.get("category", "unknown") == "unknown":
        os_info = device.get("os", device.get("os_guess", "")).lower()
        if "linux" in os_info:
            device["category"] = "linux_device"
        elif "windows" in os_info:
            device["category"] = "windows_device"
        elif "ios" in os_info or "iphone" in os_info:
            device["category"] = "mobile"
        elif "android" in os_info:
            device["category"] = "mobile"

    device.setdefault("category", "unknown")

    # Determine control method
    if 22 in ports:
        device.setdefault("control", "ssh")
    elif 80 in ports or 443 in ports or 8080 in ports:
        device.setdefault("control", "http")
    elif 1883 in ports:
        device.setdefault("control", "mqtt")

    # Label known ports
    labeled_ports = []
    for p in ports:
        label = PORT_CATEGORIES.get(p, str(p))
        labeled_ports.append({"port": p, "label": label})
    if labeled_ports:
        device["port_labels"] = labeled_ports

    return device


def _device_id(device: dict) -> str:
    """Generate stable ID from MAC or IP."""
    mac = device.get("mac", "")
    if mac:
        return sha256(mac.encode()).hexdigest()[:12]
    return sha256(device.get("ip", "").encode()).hexdigest()[:12]


# ── Scan functions ──────────────────────────────────────────────

def quick_scan(subnet: str = SUBNET) -> list:
    """Fast host discovery. Returns list of {ip, mac, vendor}."""
    _log({"action": "quick_scan", "subnet": subnet})
    output = _run_nmap(["-sn", subnet], timeout=QUICK_SCAN_TIMEOUT)
    devices = parse_quick_scan(output)
    devices = _enrich_with_arp(devices)
    _log({"action": "quick_scan_done", "hosts": len(devices)})
    return devices


def deep_scan(subnet: str = SUBNET) -> list:
    """Full fingerprint scan. Returns enriched device list. SLOW (~5 min)."""
    _log({"action": "deep_scan", "subnet": subnet})
    output = _run_nmap(
        ["-A", "--host-timeout", "30s", subnet],
        timeout=DEEP_SCAN_TIMEOUT,
    )
    devices = parse_deep_scan(output)
    devices = _enrich_with_arp(devices)
    _log({"action": "deep_scan_done", "hosts": len(devices)})
    return devices


def target_scan(ip: str) -> dict:
    """Deep scan a single IP. Returns enriched device dict."""
    _log({"action": "target_scan", "ip": ip})
    # Use -sT (connect scan, no root needed) + -sV (service version)
    # Skip -O (OS detection needs root) — use ARP + vendor instead
    output = _run_nmap(
        ["-sT", "-sV", "--host-timeout", "30s", ip],
        timeout=TARGET_SCAN_TIMEOUT,
    )
    devices = parse_deep_scan(output)
    if not devices:
        # Fallback: try ping scan only
        output = _run_nmap(["-sn", ip], timeout=15)
        devices = parse_quick_scan(output)
    devices = _enrich_with_arp(devices)
    if devices:
        return devices[0]
    return {"ip": ip, "status": "down_or_filtered"}


# ── Inventory management ────────────────────────────────────────

def update_inventory(scan_results: list, scan_type: str = "quick") -> dict:
    """Merge scan results into persistent inventory.

    Returns the updated inventory.
    """
    inventory = _load_inventory()
    now = _now()

    for device in scan_results:
        ip = device.get("ip")
        if not ip:
            continue

        existing = inventory.get(ip, {})

        # Merge: new data enriches, doesn't overwrite manual fields
        merged = {**existing}
        merged["ip"] = ip
        merged["last_seen"] = now
        merged["last_scan_type"] = scan_type

        # Update fields from scan (don't overwrite manual overrides)
        new_vendor = False
        for key in ["mac", "vendor", "hostname", "os", "os_guess", "device_type_nmap"]:
            if key in device and device[key]:
                if key == "vendor" and device[key] != existing.get(key):
                    new_vendor = True
                merged[key] = device[key]

        # Ports and services: always update from deep scan
        if scan_type == "deep" and "ports" in device:
            merged["ports"] = device["ports"]
            merged["services"] = device.get("services", [])

        # Reclassify if we got new vendor info or category is still unknown
        if new_vendor or merged.get("category", "unknown") == "unknown":
            merged.pop("category", None)  # force reclassification
        merged = classify_device(merged)

        # Stable ID
        merged["id"] = _device_id(merged)

        # First seen
        merged.setdefault("first_seen", now)

        inventory[ip] = merged

    # Mark devices not seen in this scan
    seen_ips = {d.get("ip") for d in scan_results}
    for ip, device in inventory.items():
        if ip not in seen_ips:
            device.setdefault("missing_since", now)
        else:
            device.pop("missing_since", None)

    _save_inventory(inventory)
    _log({"action": "inventory_updated", "scan_type": scan_type,
          "total": len(inventory), "new_this_scan": len(seen_ips)})
    return inventory


def get_inventory() -> list:
    """Return current inventory as sorted list."""
    inventory = _load_inventory()
    return sorted(inventory.values(), key=lambda d: _ip_sort_key(d.get("ip", "")))


def get_device(ip: str) -> dict:
    """Get a specific device from inventory."""
    inventory = _load_inventory()
    return inventory.get(ip, {})


def get_by_category(category: str) -> list:
    """Get all devices of a category."""
    inventory = _load_inventory()
    return [d for d in inventory.values() if d.get("category") == category]


def get_controllable() -> list:
    """Get devices that have a control method."""
    inventory = _load_inventory()
    return [d for d in inventory.values() if d.get("control")]


def get_new_devices(since_hours: int = 24) -> list:
    """Get devices first seen in the last N hours."""
    inventory = _load_inventory()
    cutoff = time.time() - (since_hours * 3600)
    result = []
    for d in inventory.values():
        first = d.get("first_seen", "")
        if first:
            try:
                ts = datetime.fromisoformat(first).timestamp()
                if ts > cutoff:
                    result.append(d)
            except (ValueError, TypeError):
                pass
    return result


def get_missing_devices() -> list:
    """Get devices that were seen before but not in last scan."""
    inventory = _load_inventory()
    return [d for d in inventory.values() if d.get("missing_since")]


# ── Formatting ──────────────────────────────────────────────────

def format_table(devices: list) -> str:
    """Format devices as a readable table."""
    if not devices:
        return "No devices found."

    lines = []
    lines.append(f"{'IP':<18} {'Name':<20} {'Category':<14} {'Vendor':<20} {'MAC':<18}")
    lines.append("-" * 92)

    for d in sorted(devices, key=lambda x: _ip_sort_key(x.get("ip", ""))):
        ip = d.get("ip", "?")
        name = d.get("name", d.get("hostname", "-"))[:19]
        cat = d.get("category", "?")[:13]
        vendor = d.get("vendor", "-")[:19]
        mac = d.get("mac", "-")
        lines.append(f"{ip:<18} {name:<20} {cat:<14} {vendor:<20} {mac:<18}")

    return "\n".join(lines)


def format_telegram(devices: list) -> str:
    """Format for Telegram message."""
    if not devices:
        return "No hay dispositivos en el inventario."

    by_cat = {}
    for d in devices:
        cat = d.get("category", "unknown")
        by_cat.setdefault(cat, []).append(d)

    lines = [f"Red LAN — {len(devices)} dispositivos\n"]

    cat_icons = {
        "server": "S", "network": "N", "smart_home": "H",
        "display": "D", "iot": "I", "client": "C",
        "camera": "K", "printer": "P", "media": "M",
        "mobile": "F", "phone": "F", "tablet": "T", "speaker": "A",
        "unknown": "?",
    }

    for cat in sorted(by_cat.keys()):
        icon = cat_icons.get(cat, "?")
        lines.append(f"[{icon}] {cat} ({len(by_cat[cat])})")
        for d in sorted(by_cat[cat], key=lambda x: _ip_sort_key(x.get("ip", ""))):
            name = d.get("name", d.get("hostname", d.get("ip")))
            ip = d.get("ip")
            extra = ""
            if d.get("ports"):
                extra = f" — ports: {','.join(str(p) for p in d['ports'][:5])}"
            lines.append(f"  {ip} {name}{extra}")
        lines.append("")

    missing = [d for d in devices if d.get("missing_since")]
    if missing:
        lines.append(f"Offline ({len(missing)}):")
        for d in missing:
            lines.append(f"  {d.get('ip')} {d.get('name', '?')}")

    return "\n".join(lines)


def summary() -> dict:
    """Return inventory summary stats."""
    devices = get_inventory()
    by_cat = {}
    for d in devices:
        cat = d.get("category", "unknown")
        by_cat[cat] = by_cat.get(cat, 0) + 1

    controllable = len([d for d in devices if d.get("control")])
    missing = len([d for d in devices if d.get("missing_since")])

    return {
        "total": len(devices),
        "by_category": by_cat,
        "controllable": controllable,
        "missing": missing,
    }


# ── Full discovery pipeline ─────────────────────────────────────

def discover(mode: str = "quick", subnet: str = SUBNET,
             target: str = None) -> dict:
    """Run discovery pipeline.

    Args:
        mode: "quick" (host discovery) or "deep" (full fingerprint)
        subnet: Network to scan
        target: Specific IP to scan (overrides mode)

    Returns:
        {"devices": list, "summary": dict, "scan_type": str}
    """
    if target:
        device = target_scan(target)
        if device.get("ip"):
            device = classify_device(device)
            update_inventory([device], scan_type="target")
        return {"devices": [device], "summary": summary(), "scan_type": "target"}

    if mode == "deep":
        raw = deep_scan(subnet)
    else:
        raw = quick_scan(subnet)

    classified = [classify_device(d) for d in raw]
    update_inventory(classified, scan_type=mode)

    return {
        "devices": classified,
        "summary": summary(),
        "scan_type": mode,
    }


# ── CLI ─────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Tenazas Device Discovery")
    parser.add_argument("--quick", action="store_true", help="Quick scan (host discovery)")
    parser.add_argument("--deep", action="store_true", help="Deep scan (full fingerprint)")
    parser.add_argument("--target", type=str, help="Scan specific IP")
    parser.add_argument("--inventory", action="store_true", help="Show current inventory (JSON)")
    parser.add_argument("--table", action="store_true", help="Show inventory as table")
    parser.add_argument("--telegram", action="store_true", help="Format for Telegram")
    parser.add_argument("--summary", action="store_true", help="Show summary stats")
    parser.add_argument("--new", type=int, metavar="HOURS", help="Show devices first seen in last N hours")
    parser.add_argument("--missing", action="store_true", help="Show missing devices")
    parser.add_argument("--category", type=str, help="Filter by category")
    parser.add_argument("--controllable", action="store_true", help="Show controllable devices")
    args = parser.parse_args()

    if args.inventory:
        devices = get_inventory()
        print(json.dumps(devices, indent=2, default=str))
    elif args.table:
        print(format_table(get_inventory()))
    elif args.telegram:
        print(format_telegram(get_inventory()))
    elif args.summary:
        print(json.dumps(summary(), indent=2))
    elif args.new is not None:
        devices = get_new_devices(args.new)
        print(format_table(devices))
    elif args.missing:
        devices = get_missing_devices()
        print(format_table(devices))
    elif args.category:
        devices = get_by_category(args.category)
        print(format_table(devices))
    elif args.controllable:
        devices = get_controllable()
        print(format_table(devices))
    elif args.target:
        result = discover(target=args.target)
        print(json.dumps(result["devices"], indent=2, default=str))
    elif args.deep:
        print("Running deep scan (this takes ~5 min)...")
        result = discover(mode="deep")
        print(f"Found {len(result['devices'])} devices")
        print(format_table(result["devices"]))
        print(f"\nSummary: {json.dumps(result['summary'], indent=2)}")
    else:
        # Default: quick scan
        result = discover(mode="quick")
        print(f"Found {len(result['devices'])} devices")
        print(format_table(result["devices"]))


if __name__ == "__main__":
    main()

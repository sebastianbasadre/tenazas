#!/usr/bin/env python3
"""
Network Exporter — Prometheus metrics from device_inventory.json.

Runs as a persistent process on :9108/metrics.
Reads device_inventory.json every 30s and exports per-device info labels
plus aggregate gauges.

Usage: python3 network_exporter.py &

Metrics:
  tenazas_devices_total          — total devices in inventory
  tenazas_devices_online         — online in last scan
  tenazas_devices_missing        — missing from last scan
  tenazas_devices_classified     — with known category (not unknown)
  tenazas_devices_controllable   — with control method
  tenazas_device_info{ip,name,vendor,category,mac,control} — 1 per device
  tenazas_device_online{ip,name} — 1 if online, 0 if missing
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("network_exporter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR = Path(__file__).parent
INVENTORY_FILE = BASE_DIR / "device_inventory.json"
POLL_INTERVAL = 30  # seconds
PORT = 9108

try:
    from prometheus_client import Gauge, Info, start_http_server
    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False
    log.error("prometheus_client not installed")

if PROM_AVAILABLE:
    # Aggregate gauges
    G_TOTAL = Gauge("tenazas_devices_total", "Total devices in inventory")
    G_ONLINE = Gauge("tenazas_devices_online", "Devices seen in last scan")
    G_MISSING = Gauge("tenazas_devices_missing", "Devices missing from last scan")
    G_CLASSIFIED = Gauge("tenazas_devices_classified", "Devices with known category")
    G_CONTROLLABLE = Gauge("tenazas_devices_controllable", "Devices with control method")

    # Per-device info (value=1, labels carry the data)
    G_DEVICE_INFO = Gauge(
        "tenazas_device_info",
        "Device inventory entry (1=present)",
        ["ip", "name", "vendor", "category", "mac", "control"],
    )
    G_DEVICE_ONLINE = Gauge(
        "tenazas_device_online",
        "Device online status (1=online, 0=missing)",
        ["ip", "name"],
    )

    # Deco WiFi metrics (populated if deco_client data available)
    G_DECO_CLIENTS = Gauge("tenazas_deco_clients_total", "Total WiFi clients on Deco mesh")
    G_DECO_BAND = Gauge(
        "tenazas_deco_client_band",
        "Client connected to band (1=connected)",
        ["ip", "name", "band"],
    )
    G_DECO_SIGNAL = Gauge(
        "tenazas_deco_client_signal",
        "Client WiFi signal strength (dBm or %)",
        ["ip", "name"],
    )
    G_DECO_CONNECTION = Gauge(
        "tenazas_deco_by_connection",
        "Devices by connection type",
        ["connection_type"],
    )


def _load_inventory() -> list:
    try:
        with open(INVENTORY_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def _load_deco_data() -> list:
    """Load Deco client data if available."""
    deco_file = BASE_DIR / "deco_clients.json"
    try:
        with open(deco_file) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


# Track previous label sets to clear stale series
_prev_device_labels = set()
_prev_deco_labels = set()


def update_metrics():
    """Read inventory and update all Prometheus metrics."""
    global _prev_device_labels, _prev_deco_labels

    if not PROM_AVAILABLE:
        return

    devices = _load_inventory()

    # Aggregates
    total = len(devices)
    missing = sum(1 for d in devices if d.get("missing_since"))
    online = total - missing
    classified = sum(1 for d in devices if d.get("category", "unknown") != "unknown")
    controllable = sum(1 for d in devices if d.get("control"))

    G_TOTAL.set(total)
    G_ONLINE.set(online)
    G_MISSING.set(missing)
    G_CLASSIFIED.set(classified)
    G_CONTROLLABLE.set(controllable)

    # Clear previous device_info labels
    for labels in _prev_device_labels:
        try:
            G_DEVICE_INFO.remove(*labels)
        except Exception:
            pass
    new_device_labels = set()

    # Per-device info
    for d in devices:
        ip = d.get("ip", "")
        name = d.get("name", d.get("hostname", ip))[:30]
        vendor = d.get("vendor", "-")[:20]
        category = d.get("category", "unknown")
        mac = d.get("mac", "-")
        control = d.get("control", "-") or "-"
        is_missing = bool(d.get("missing_since"))

        labels = (ip, name, vendor, category, mac, control)
        G_DEVICE_INFO.labels(*labels).set(1)
        new_device_labels.add(labels)

        G_DEVICE_ONLINE.labels(ip=ip, name=name).set(0 if is_missing else 1)

    _prev_device_labels = new_device_labels

    # Deco WiFi data
    deco_clients = _load_deco_data()
    if deco_clients:
        G_DECO_CLIENTS.set(len(deco_clients))

        # Clear previous deco labels
        for labels in _prev_deco_labels:
            try:
                G_DECO_BAND.remove(*labels)
            except Exception:
                pass
        new_deco_labels = set()

        by_conn = {}
        for c in deco_clients:
            ip = c.get("ip", "")
            name = c.get("name", ip)[:30]
            band = c.get("band", "unknown")
            signal = c.get("signal", 0)
            conn_type = c.get("connection_type", "wifi")

            labels = (ip, name, band)
            G_DECO_BAND.labels(*labels).set(1)
            new_deco_labels.add(labels)

            if signal:
                G_DECO_SIGNAL.labels(ip=ip, name=name).set(signal)

            by_conn[conn_type] = by_conn.get(conn_type, 0) + 1

        for conn_type, count in by_conn.items():
            G_DECO_CONNECTION.labels(connection_type=conn_type).set(count)

        _prev_deco_labels = new_deco_labels


if __name__ == "__main__":
    if not PROM_AVAILABLE:
        print("prometheus_client not available")
        exit(1)

    start_http_server(PORT)
    log.info("Network exporter on :%d (polling device_inventory.json every %ds)", PORT, POLL_INTERVAL)

    while True:
        try:
            update_metrics()
        except Exception as e:
            log.warning("Update failed: %s", e)
        time.sleep(POLL_INTERVAL)

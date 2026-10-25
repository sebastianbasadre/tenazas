"""One NAS-side sanitized source boundary. Importing opens no listener or collectors.

HTTP serves only prevalidated in-memory documents. Collection happens separately;
no URL/path/command supplied by a client ever reaches a collector.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from workspace.runtime.mcp_snapshot_contract import MAX_BYTES
from workspace.runtime.mcp_source_contract import (
    storage_payload,
    validate_feed,
)

ROUTES = ("/inventory", "/storage", "/network")
MEDIA_ROUTE = "/media"
AUDIT = logging.getLogger("tenazas.mcp.sources")


NETWORK_METRICS_URL = "http://192.168.2.2:9105/metrics"
NETWORK_METRICS = {
    "tenazas_network_last_scan_timestamp",
    "device_discovery_online",
    "device_discovery_missing",
}


def fetch_network_metrics():
    from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    with build_opener(ProxyHandler({}), NoRedirect()).open(
        Request(NETWORK_METRICS_URL, method="GET"), timeout=5
    ) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("Metrics size limit")
    return payload


def network_from_metrics(payload):
    import math

    if not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
        raise ValueError("Invalid metrics payload")
    samples = {}
    for line in payload.decode("utf-8").splitlines():
        fields = line.split()
        if not fields or fields[0] not in NETWORK_METRICS:
            continue
        if len(fields) not in (2, 3) or fields[0] in samples:
            raise ValueError("Ambiguous metric sample")
        value = float(fields[1])
        if not math.isfinite(value) or value < 0 or value > 2**53:
            raise ValueError("Invalid metric sample")
        samples[fields[0]] = value
    if set(samples) != NETWORK_METRICS:
        raise ValueError("Missing authoritative network metrics")
    epoch = samples["tenazas_network_last_scan_timestamp"]
    if epoch <= 0:
        raise ValueError("Scan timestamp unavailable")
    counts = [samples[k] for k in ("device_discovery_online", "device_discovery_missing")]
    if any(not count.is_integer() for count in counts):
        raise ValueError("Non-integer device count")
    online, missing = map(int, counts)
    observed = datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")
    return validate_feed(
        "/network",
        {
            "observed_at": observed,
            "total": online + missing,
            "online": online,
            "missing": missing,
            "healthy": None,
            "unhealthy": None,
            "latency_ms": None,
        },
    )


class NASCollectors:
    """Explicit NAS-only configuration. No rootfs fallback or VM Docker use."""

    def __init__(
        self, *, filesystem, filesystem_confirmed, inventory_collector=None, metrics_fetch=None
    ):
        path = Path(filesystem)
        if filesystem_confirmed is not True or not path.is_absolute() or path == Path("/"):
            raise ValueError("Confirmed NAS volume required")
        self.filesystem = path
        self.metrics_fetch = metrics_fetch or fetch_network_metrics
        if inventory_collector is None:
            from tools.mcp_nas_inventory import inventory

            inventory_collector = inventory
        self.inventory_collector = inventory_collector

    def inventory(self):
        return validate_feed("/inventory", self.inventory_collector())

    def storage(self):
        path = self.filesystem
        if (
            path.resolve(strict=True) != path
            or not os.path.ismount(path)
            or any(
                path == Path(prefix) or Path(prefix) in path.parents
                for prefix in ("/proc", "/sys", "/dev", "/run")
            )
        ):
            raise ValueError("Confirmed NAS mount unavailable")
        # Reuse the NAS metrics arithmetic, but on the explicitly confirmed mount.
        info = os.statvfs(path)
        total = info.f_frsize * info.f_blocks
        free = info.f_frsize * info.f_bavail
        if total == 0:
            raise ValueError("NAS capacity unavailable")
        observed = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return storage_payload(total, total - free, observed)

    def network(self):
        return network_from_metrics(self.metrics_fetch())


class SourceStore:
    def __init__(self, media_file=None):
        self._lock = threading.Lock()
        self._documents = {}
        self.media_file = media_file

    def refresh(self, collectors):
        documents = {}
        for route in ROUTES:
            try:
                value = validate_feed(route, getattr(collectors, route[1:])())
                raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
                if len(raw) > MAX_BYTES:
                    raise ValueError()
                documents[route] = raw
            except Exception:
                # Producer retains its last valid value; never renew failed data.
                documents[route] = None
        if self.media_file is not None:
            try:
                from workspace.runtime.mcp_media_reader import read_state

                documents[MEDIA_ROUTE] = json.dumps(read_state(self.media_file), allow_nan=False).encode()
            except Exception:
                documents[MEDIA_ROUTE] = None
        with self._lock:
            self._documents = documents

    def get(self, route):
        with self._lock:
            return self._documents.get(route)


def validate_private_address(address):
    bind, port = address
    ip = ipaddress.ip_address(bind)
    networks = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    if (
        ip.version != 4
        or not (ip.is_loopback or any(ip in network for network in networks))
        or type(port) is not int
        or not 0 <= port <= 65535
    ):
        raise ValueError("Explicit private IPv4 bind required")


class SourceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store):
        validate_private_address(address)
        self.store = store
        self._slots = threading.BoundedSemaphore(8)
        super().__init__(address, SourceHandler)

    def handle_error(self, request, client_address):
        AUDIT.warning("source_transport_error")

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class SourceHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TenazasSanitizedSources"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, b'{"error":"invalid request"}')

    def reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True
        AUDIT.info("source_request status=%d", status)

    def do_GET(self):
        if self.path not in (*ROUTES, MEDIA_ROUTE) or (self.path == MEDIA_ROUTE and self.server.store.media_file is None):
            return self.reply(404, b'{"error":"not found"}')
        if (
            self.headers.get_all("Origin")
            or self.headers.get_all("Transfer-Encoding")
            or self.headers.get_all("Content-Length")
        ):
            return self.reply(400, b'{"error":"invalid request"}')
        value = self.server.store.get(self.path)
        return (
            self.reply(200, value)
            if value is not None
            else self.reply(503, b'{"error":"source unavailable"}')
        )

    def forbidden(self):
        self.reply(405, b'{"error":"method forbidden"}')

    def do_POST(self):
        self.forbidden()

    def do_PUT(self):
        self.forbidden()

    def do_PATCH(self):
        self.forbidden()

    def do_DELETE(self):
        self.forbidden()

    def do_OPTIONS(self):
        self.forbidden()

    def do_HEAD(self):
        self.forbidden()


def refresh_until_stopped(store, collectors, stop, interval=30):
    """Future NAS lifecycle hook only; no listener/refresh starts automatically."""
    if type(interval) not in (int, float) or not 1 <= interval <= 3600:
        raise ValueError("Invalid refresh interval")
    while not stop.is_set():
        store.refresh(collectors)
        stop.wait(interval)


def main():
    """Future explicit NAS launch only. No bind address, port or volume defaults."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--filesystem", type=Path, required=True)
    parser.add_argument("--media-file", type=Path)
    parser.add_argument("--filesystem-confirmed", action="store_true", required=True)
    args = parser.parse_args()
    if args.filesystem != Path("/volume1"):
        parser.error("Validated NAS storage target must be /volume1")
    if not 1 <= args.port <= 65535:
        parser.error("Explicit private service port required")
    validate_private_address((args.bind, args.port))
    collectors = NASCollectors(
        filesystem=args.filesystem,
        filesystem_confirmed=args.filesystem_confirmed,
    )
    # A confirmed but missing/wrong mount must fail before any listener opens.
    try:
        collectors.storage()
    except Exception:
        AUDIT.error("source_volume_unavailable")
        return 1
    store = SourceStore(args.media_file)
    store.refresh(collectors)
    server = SourceServer((args.bind, args.port), store)
    stop = threading.Event()
    worker = threading.Thread(
        target=refresh_until_stopped, args=(store, collectors, stop), daemon=True
    )
    worker.start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

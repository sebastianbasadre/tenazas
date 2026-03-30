#!/usr/bin/env python3
"""
Standalone Prometheus metrics server for Tenazas.

Runs as a persistent background process. The runtime loop
updates shared metrics via the prometheus_client registry.

Usage: python3 metrics_server.py &
"""

import time
from runtime.metrics_exporter import start_server, PROM_AVAILABLE

if __name__ == "__main__":
    if not PROM_AVAILABLE:
        print("prometheus_client not available")
        exit(1)

    ok = start_server()
    if ok:
        print("Metrics server on :9105")
        while True:
            time.sleep(3600)
    else:
        print("Failed to start metrics server")
        exit(1)

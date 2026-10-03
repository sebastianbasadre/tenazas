"""Keep the API metrics exporter alive without allocating an unbounded list."""

import time

from api_call_counter import start_exporter

if __name__ == "__main__":
    start_exporter()
    while True:
        time.sleep(3600)

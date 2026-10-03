#!/usr/bin/env python3

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if (_C / "finance_exporter.py").is_file():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


class TestFinanceExporterNotifications(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(RUNTIME.parent))
        sys.path.insert(0, str(RUNTIME))
        os.environ.pop("ENABLE_FINANCE_EXPORTER_TELEGRAM", None)

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "finance_exporter",
            RUNTIME / "finance_exporter.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["finance_exporter"] = module
        spec.loader.exec_module(module)
        cls.exporter = module

    def tearDown(self):
        os.environ.pop("ENABLE_FINANCE_EXPORTER_TELEGRAM", None)
        self.exporter.ENABLE_FINANCE_EXPORTER_TELEGRAM = False
        self.exporter._last_pnl_notify_hour = -1

    def test_hourly_binance_telegram_is_disabled_by_default(self):
        exporter = self.exporter
        exporter.ENABLE_FINANCE_EXPORTER_TELEGRAM = False

        with patch.object(exporter, "_send_telegram") as send:
            exporter._maybe_send_hourly_binance({"total_usd": 100, "assets": []})

        send.assert_not_called()

    def test_hourly_binance_telegram_can_be_enabled(self):
        exporter = self.exporter
        exporter.ENABLE_FINANCE_EXPORTER_TELEGRAM = True
        exporter._last_pnl_notify_hour = -1

        with patch.object(exporter, "fetch_fear_greed", return_value=9), \
                patch.object(exporter, "_send_telegram") as send:
            exporter._maybe_send_hourly_binance(
                {
                    "total_usd": 100,
                    "assets": [
                        {
                            "asset": "BTC",
                            "price": 61000,
                            "value_usd": 100,
                            "change_24h_pct": 0.2,
                        }
                    ],
                }
            )

        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()

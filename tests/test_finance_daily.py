#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))

import finance_daily


class TestFinanceDailyIOLNormalization(unittest.TestCase):
    def test_iol_change_keeps_percentage_scale_when_points_match_percent(self):
        pct = finance_daily._normalize_iol_change_pct(0.06, 6.07, 10118.0)
        self.assertAlmostEqual(pct, 0.06, places=2)

    def test_iol_change_converts_fractional_scale_when_points_match_fraction(self):
        pct = finance_daily._normalize_iol_change_pct(0.06, 6.0, 100.0)
        self.assertAlmostEqual(pct, 6.0, places=2)

    def test_format_daily_uses_normalized_iol_pnl(self):
        with patch.object(finance_daily, "datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "04/05/2026"
            text = finance_daily.format_daily(
                None,
                {
                    "total_ars": 10118,
                    "pnl_day_ars": 607,
                    "portfolio_currency": "ARS",
                    "positions": [
                        {
                            "symbol": "TEST",
                            "raw_symbol": "TEST",
                            "value": 10118,
                            "currency": "ARS",
                            "change_pct": 6.0,
                        }
                    ],
                },
            )
        self.assertIn("IOL: $10,118 ARS | hoy: +$607", text)
        self.assertIn("TEST: $10,118 ARS (+6.0%)", text)

    def test_format_daily_shows_usd_for_usd_iol_positions(self):
        with patch.object(finance_daily, "datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "04/05/2026"
            text = finance_daily.format_daily(
                None,
                {
                    "total_ars": 10118,
                    "pnl_day_ars": 5.5,
                    "portfolio_currency": "USD",
                    "positions": [
                        {
                            "symbol": "IOLUSD",
                            "raw_symbol": "IOLUSD",
                            "value": 10118,
                            "currency": "USD",
                            "change_pct": 0.02,
                        }
                    ],
                },
            )
        self.assertIn("IOL: US$10,118 | hoy: +US$6", text)
        self.assertIn("IOLUSD: US$10,118 (+0.02%)", text)

    def test_save_snapshot_for_explicit_date_keeps_currency_and_source(self):
        with TemporaryDirectory() as tmp:
            history_dir = Path(tmp)
            with patch.object(finance_daily, "HISTORY_DIR", history_dir):
                path = finance_daily.save_snapshot_for_date(
                    "2026-05-05",
                    {
                        "total_usd": 9220,
                        "assets": [{"asset": "BTC", "value_usd": 9218, "change_pct": 2.86}],
                    },
                    {
                        "total_ars": 10113,
                        "pnl_day_ars": -9,
                        "portfolio_currency": "USD",
                        "positions": [],
                    },
                    source="manual",
                )

            self.assertTrue(path.exists())
            payload = path.read_text()
            self.assertIn('"date": "2026-05-05"', payload)
            self.assertIn('"source": "manual"', payload)
            self.assertIn('"portfolio_currency": "USD"', payload)


if __name__ == "__main__":
    unittest.main()

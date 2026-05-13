#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))

from finance_positions import build_unified_positions


class TestFinancePositions(unittest.TestCase):
    def test_iol_usd_position_keeps_native_usd_value(self):
        positions = build_unified_positions(
            None,
            {
                "positions": [
                    {
                        "symbol": "IOLUSD",
                        "quantity": 10,
                        "price": 100,
                        "value": 1000,
                        "currency": "USD",
                        "pnl_day": 5,
                        "change_pct": 0.5,
                        "type": "Fondo",
                    }
                ]
            },
            1400,
        )
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["value_usd"], 1000)
        self.assertEqual(positions[0]["pnl_day_usd"], 5)
        self.assertEqual(positions[0]["currency"], "USD")

    def test_iol_ars_position_converts_to_usd(self):
        positions = build_unified_positions(
            None,
            {
                "positions": [
                    {
                        "symbol": "GGAL",
                        "quantity": 2,
                        "price": 5000,
                        "value": 10000,
                        "currency": "ARS",
                        "pnl_day": 140,
                        "change_pct": 1.4,
                        "type": "Stock",
                    }
                ]
            },
            1000,
        )
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["value_usd"], 10)
        self.assertEqual(positions[0]["pnl_day_usd"], 0.14)
        self.assertEqual(positions[0]["currency"], "ARS")


if __name__ == "__main__":
    unittest.main()

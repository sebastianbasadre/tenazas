#!/usr/bin/env python3

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


class TestAgentFinance(unittest.TestCase):
    def test_observe_binance_uses_btc_asset_fields(self):
        import agent_finance as af
        mock_fd = MagicMock()
        mock_fd.get_binance_pnl = MagicMock(return_value={
            "total_usd": 9220,
            "assets": [
                {"asset": "BTC", "price": 9218, "change_pct": 2.86, "value_usd": 9218},
            ],
        })
        mock_fd.get_iol_portfolio = MagicMock(return_value=None)
        with patch.dict("sys.modules", {"finance_daily": mock_fd}):
            importlib.reload(af)
            obs, _recs = af.handle("binance")
        self.assertIn("BTC $9,218", obs.summary)
        self.assertIn("+2.9%", obs.summary)

    def test_observe_iol_uses_new_currency_aware_keys(self):
        import agent_finance as af
        mock_fd = MagicMock()
        mock_fd.get_binance_pnl = MagicMock(return_value=None)
        mock_fd.get_iol_portfolio = MagicMock(return_value={
            "total_ars": 10113,
            "pnl_day_ars": -9,
            "portfolio_currency": "USD",
        })
        with patch.dict("sys.modules", {"finance_daily": mock_fd}):
            importlib.reload(af)
            obs, _recs = af.handle("iol")
        self.assertIn("IOL: US$10,113 total", obs.summary)


if __name__ == "__main__":
    unittest.main()

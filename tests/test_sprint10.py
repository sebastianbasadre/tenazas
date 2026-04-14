#!/usr/bin/env python3
"""
test_sprint10.py — Autonomous Trading (Safe Phases Only).

CRITICAL SAFETY TESTS: No module can execute real orders or access API keys.
"""

import json
import inspect
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


# ══════════════════════════════════════════════════════════════
#  SAFETY: No real trading, no API keys
# ══════════════════════════════════════════════════════════════

class TestTradingSafety(unittest.TestCase):
    """These tests MUST ALWAYS pass. They prevent real money loss."""

    TRADING_MODULES = [
        "trading_observer.py", "trading_journal.py", "trading_hypothesis.py",
        "trading_paper.py", "trading_scorer.py",
    ]

    def test_no_module_imports_api_keys(self):
        """No trading module uses binance_api_key or binance_secret in code (not comments)."""
        for mod_file in self.TRADING_MODULES:
            content = (RUNTIME / mod_file).read_text()
            # Strip comments and docstrings, check only code lines
            code_lines = []
            in_docstring = False
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    in_docstring = not in_docstring
                    continue
                if in_docstring or stripped.startswith("#"):
                    continue
                code_lines.append(line)
            code = "\n".join(code_lines)
            self.assertNotIn("binance_api_key", code,
                             f"{mod_file} uses binance_api_key in code!")
            self.assertNotIn("binance_secret", code,
                             f"{mod_file} uses binance_secret in code!")

    def test_no_module_has_order_endpoints(self):
        """No trading module calls Binance order endpoints."""
        ORDER_PATTERNS = ["/api/v3/order", "/api/v3/openOrders",
                          "order?", "newOrder", "testOrder"]
        for mod_file in self.TRADING_MODULES:
            content = (RUNTIME / mod_file).read_text()
            for pattern in ORDER_PATTERNS:
                self.assertNotIn(pattern, content,
                                 f"{mod_file} has order endpoint: {pattern}")

    def test_no_module_has_trading_functions(self):
        """No module defines buy/sell/execute_order functions."""
        BANNED = ["def buy", "def sell", "def execute_order",
                  "def place_order", "def submit_order"]
        for mod_file in self.TRADING_MODULES:
            content = (RUNTIME / mod_file).read_text()
            for func in BANNED:
                self.assertNotIn(func, content,
                                 f"{mod_file} has banned function: {func}")

    def test_paper_uses_only_public_endpoints(self):
        """trading_paper.py only uses public price data."""
        content = (RUNTIME / "trading_paper.py").read_text()
        self.assertIn("get_latest", content)  # uses trading_observer
        self.assertNotIn("POST", content)

    def test_scorer_never_auto_activates(self):
        """trading_scorer never has code to activate phase 6."""
        content = (RUNTIME / "trading_scorer.py").read_text()
        self.assertNotIn("def activate", content)
        self.assertNotIn("def enable_real", content)
        self.assertNotIn("def go_live", content)
        self.assertNotIn("execute_real_trade", content)

    def test_observer_only_public_urls(self):
        """trading_observer uses only public Binance/alternative.me URLs."""
        content = (RUNTIME / "trading_observer.py").read_text()
        self.assertIn("api/v3/ticker/24hr", content)
        self.assertIn("api.alternative.me", content)
        self.assertNotIn("X-MBX-APIKEY", content)


# ══════════════════════════════════════════════════════════════
#  trading_observer
# ══════════════════════════════════════════════════════════════

class TestTradingObserver(unittest.TestCase):

    def test_parse_ticker(self):
        import trading_observer as to
        mock_data = {
            "lastPrice": "87500.00", "priceChangePercent": "2.50",
            "volume": "12345.67", "highPrice": "88000", "lowPrice": "85000",
        }
        with patch.object(to, "_fetch_json", return_value=mock_data):
            result = to.fetch_ticker("BTCUSDT")
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["price"], 87500.0)
        self.assertAlmostEqual(result["change_pct"], 2.5)

    def test_parse_fear_greed(self):
        import trading_observer as to
        mock_data = {"data": [{"value": "25", "value_classification": "Extreme Fear"}]}
        with patch.object(to, "_fetch_json", return_value=mock_data):
            result = to.fetch_fear_greed()
        self.assertEqual(result["value"], 25)
        self.assertEqual(result["classification"], "Extreme Fear")

    def test_moving_averages(self):
        import trading_observer as to
        obs = [{"tickers": {"BTCUSDT": {"price": 80000 + i * 100}}} for i in range(30)]
        mas = to.calc_moving_averages(obs)
        self.assertGreater(mas["ma7"], 0)
        self.assertGreater(mas["ma30"], 0)
        self.assertGreater(mas["ma7"], mas["ma30"])  # uptrend

    def test_volatility_calculation(self):
        import trading_observer as to
        obs = [{"tickers": {"BTCUSDT": {"price": 80000 + (i % 5) * 500}}} for i in range(24)]
        vol = to.calc_volatility(obs)
        self.assertGreater(vol, 0)

    def test_trend_classification(self):
        import trading_observer as to
        self.assertEqual(to.classify_trend(90000, 88000, 85000), "bullish")
        self.assertEqual(to.classify_trend(80000, 82000, 85000), "bearish")
        self.assertEqual(to.classify_trend(85000, 86000, 84000), "sideways")

    def test_volatility_classification(self):
        import trading_observer as to
        self.assertEqual(to.classify_volatility(3000, 80000), "high")
        self.assertEqual(to.classify_volatility(1000, 80000), "medium")
        self.assertEqual(to.classify_volatility(100, 80000), "low")

    def test_observe_saves_data(self):
        import trading_observer as to
        tmpdir = tempfile.mkdtemp()
        orig = to.OBS_FILE
        to.OBS_FILE = Path(tmpdir) / "obs.json"
        mock_ticker = {"lastPrice": "85000", "priceChangePercent": "1.5",
                       "volume": "1000", "highPrice": "86000", "lowPrice": "84000"}
        with patch.object(to, "_fetch_json", return_value=mock_ticker):
            to.observe()
        self.assertTrue(to.OBS_FILE.exists())
        to.OBS_FILE = orig
        shutil.rmtree(tmpdir)

    def test_fetch_failure_no_crash(self):
        import trading_observer as to
        with patch.object(to, "_fetch_json", return_value=None):
            result = to.fetch_ticker("BTCUSDT")
        self.assertIsNone(result)


# ══════════════════════════════════════════════════════════════
#  trading_journal
# ══════════════════════════════════════════════════════════════

class TestTradingJournal(unittest.TestCase):

    def test_entry_has_all_fields(self):
        import trading_journal as tj
        tmpdir = tempfile.mkdtemp()
        orig = tj.JOURNAL_DIR
        tj.JOURNAL_DIR = Path(tmpdir)
        with patch("trading_observer.get_latest", return_value={
            "tickers": {"BTCUSDT": {"price": 85000, "change_pct": 2.5},
                        "ETHUSDT": {"price": 3200, "change_pct": 1.0}},
            "fear_greed": {"value": 65},
            "derived": {"trend": "bullish", "volatility_class": "medium", "ma30": 80000},
        }):
            with patch("trading_observer.get_history", return_value=[
                {"tickers": {"BTCUSDT": {"price": 84000}}}
            ]):
                with patch("finance_daily.get_binance_pnl", return_value=None):
                    with patch("trading_journal._generate_notes", return_value="test note"):
                        entry = tj.generate_entry("2026-04-14")
        self.assertEqual(entry["date"], "2026-04-14")
        self.assertIn("btc_close", entry)
        self.assertIn("fear_greed", entry)
        self.assertIn("trend", entry)
        self.assertIn("notes", entry)
        tj.JOURNAL_DIR = orig
        shutil.rmtree(tmpdir)

    def test_format_telegram(self):
        import trading_journal as tj
        entry = {"date": "2026-04-14", "btc_close": 85000, "btc_change_pct": 2.5,
                 "eth_close": 3200, "eth_change_pct": 1.0, "fear_greed": 65,
                 "trend": "bullish", "volatility": "medium", "notable_events": [],
                 "my_portfolio_value": 769, "my_portfolio_change_pct": 4.9, "notes": "ok"}
        text = tj.format_telegram(entry)
        self.assertIn("85,000", text)
        self.assertIn("bullish", text)


# ══════════════════════════════════════════════════════════════
#  trading_hypothesis
# ══════════════════════════════════════════════════════════════

class TestTradingHypothesis(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import trading_hypothesis as th
        self._orig = th.HYPOTHESES_FILE
        th.HYPOTHESES_FILE = Path(self.tmpdir) / "hyp.json"
        self.th = th

    def tearDown(self):
        self.th.HYPOTHESES_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_extreme_fear_generates_bullish(self):
        obs = {
            "tickers": {"BTCUSDT": {"price": 75000}},
            "fear_greed": {"value": 20},
            "derived": {"trend": "bearish", "ma7": 78000, "ma30": 80000,
                        "volatility_class": "medium"},
        }
        hyps = self.th.generate(obs)
        bullish = [h for h in hyps if h["direction"] == "up"]
        self.assertTrue(len(bullish) > 0)

    def test_extreme_greed_generates_bearish(self):
        obs = {
            "tickers": {"BTCUSDT": {"price": 95000}},
            "fear_greed": {"value": 80},
            "derived": {"trend": "bullish", "ma7": 93000, "ma30": 88000,
                        "volatility_class": "medium"},
        }
        hyps = self.th.generate(obs)
        bearish = [h for h in hyps if h["direction"] == "down"]
        self.assertTrue(len(bearish) > 0)

    def test_hypothesis_has_required_fields(self):
        obs = {
            "tickers": {"BTCUSDT": {"price": 75000}},
            "fear_greed": {"value": 20},
            "derived": {"trend": "bearish", "ma7": 78000, "ma30": 80000,
                        "volatility_class": "medium"},
        }
        hyps = self.th.generate(obs)
        for h in hyps:
            for field in ("hypothesis_id", "asset", "direction", "target_price",
                          "timeframe_days", "confidence", "reasoning", "status"):
                self.assertIn(field, h)
            self.assertEqual(h["status"], "active")

    def test_evaluate_hit(self):
        self.th._save_hypotheses([{
            "hypothesis_id": "test1", "asset": "BTC", "direction": "up",
            "entry_price": 80000, "target_price": 85000,
            "timeframe_days": 7, "confidence": 0.7,
            "reasoning": "test",
            "created_at": (datetime.now().astimezone() - timedelta(days=8)).isoformat(),
            "status": "active", "result": None,
        }])
        mock_latest = {"tickers": {"BTCUSDT": {"price": 86000}}}
        with patch("trading_observer.get_latest", return_value=mock_latest):
            evaluated = self.th.evaluate()
        self.assertEqual(len(evaluated), 1)
        self.assertEqual(evaluated[0]["status"], "hit")

    def test_evaluate_missed(self):
        self.th._save_hypotheses([{
            "hypothesis_id": "test2", "asset": "BTC", "direction": "up",
            "entry_price": 80000, "target_price": 90000,
            "timeframe_days": 7, "confidence": 0.6,
            "reasoning": "test",
            "created_at": (datetime.now().astimezone() - timedelta(days=8)).isoformat(),
            "status": "active", "result": None,
        }])
        mock_latest = {"tickers": {"BTCUSDT": {"price": 82000}}}
        with patch("trading_observer.get_latest", return_value=mock_latest):
            evaluated = self.th.evaluate()
        self.assertEqual(evaluated[0]["status"], "missed")

    def test_max_active_respected(self):
        obs = {
            "tickers": {"BTCUSDT": {"price": 75000}},
            "fear_greed": {"value": 20},
            "derived": {"trend": "bearish", "ma7": 78000, "ma30": 80000,
                        "volatility_class": "medium"},
        }
        # Pre-fill 10 active
        existing = [{
            "hypothesis_id": f"h{i}", "asset": "BTC", "direction": "up",
            "target_price": 90000, "timeframe_days": 7, "confidence": 0.7,
            "reasoning": "fill", "status": "active", "result": None,
            "created_at": datetime.now().astimezone().isoformat(),
        } for i in range(10)]
        self.th._save_hypotheses(existing)
        new = self.th.generate(obs)
        self.assertEqual(len(new), 0)

    def test_accuracy_calculation(self):
        self.th._save_hypotheses([
            {"status": "hit"}, {"status": "hit"}, {"status": "missed"},
        ])
        acc = self.th.get_accuracy()
        self.assertAlmostEqual(acc["accuracy"], 0.6667, places=2)


# ══════════════════════════════════════════════════════════════
#  trading_paper
# ══════════════════════════════════════════════════════════════

class TestTradingPaper(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import trading_paper as tp
        self._orig_t = tp.TRADES_FILE
        self._orig_p = tp.PORTFOLIO_FILE
        tp.TRADES_FILE = Path(self.tmpdir) / "trades.json"
        tp.PORTFOLIO_FILE = Path(self.tmpdir) / "port.json"
        self.tp = tp

    def tearDown(self):
        self.tp.TRADES_FILE = self._orig_t
        self.tp.PORTFOLIO_FILE = self._orig_p
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _open_trade(self, hyp_id="h1", price=85000):
        """Helper to open a trade with mocked price."""
        # Reset portfolio
        self.tp._save(self.tp.PORTFOLIO_FILE,
                      {"balance": 10000.0, "initial": 10000.0, "trades_count": 0})
        with patch("trading_paper._get_current_price", return_value=price):
            return self.tp.open_trade({
                "hypothesis_id": hyp_id, "asset": "BTC", "direction": "up",
                "target_price": 90000, "confidence": 0.8,
            })

    def test_max_10pct_per_trade(self):
        """Never allocate more than 10% of portfolio per trade."""
        trade = self._open_trade()
        self.assertIsNotNone(trade)
        self.assertLessEqual(trade["size_usdt"], 10000 * 0.10 + 1)

    def test_stop_loss_set(self):
        trade = self._open_trade()
        self.assertLess(trade["stop_loss"], trade["entry_price"])

    def test_never_calls_binance_auth(self):
        """Paper trading NEVER uses authenticated APIs in code."""
        content = (RUNTIME / "trading_paper.py").read_text()
        # Check code lines only (skip comments/docstrings)
        code_lines = [l for l in content.split("\n")
                      if l.strip() and not l.strip().startswith("#")
                      and not l.strip().startswith('"""') and not l.strip().startswith("'''")]
        code = "\n".join(code_lines)
        self.assertNotIn("X-MBX-APIKEY", code)

    def test_initial_balance_10000(self):
        self.assertEqual(self.tp.INITIAL_BALANCE, 10000.0)

    def test_fee_deducted(self):
        trade = self._open_trade("h2")
        self.assertGreater(trade["fee_paid"], 0)

    def test_portfolio_balance_reduced(self):
        self._open_trade("h3")
        port = self.tp._get_portfolio()
        self.assertLess(port["balance"], 10000)

    def test_check_trades_closes_on_sl(self):
        self._open_trade("h4")
        with patch("trading_paper._get_current_price", return_value=80000):
            closed = self.tp.check_trades()
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["status"], "closed_sl")
        self.assertLess(closed[0]["pnl_usdt"], 0)

    def test_check_trades_closes_on_tp(self):
        self._open_trade("h5")
        with patch("trading_paper._get_current_price", return_value=91000):
            closed = self.tp.check_trades()
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["status"], "closed_profit")
        self.assertGreater(closed[0]["pnl_usdt"], 0)

    def test_portfolio_summary(self):
        self.tp._save(self.tp.PORTFOLIO_FILE,
                      {"balance": 10000.0, "initial": 10000.0, "trades_count": 0})
        summary = self.tp.get_portfolio_summary()
        self.assertIn("balance", summary)
        self.assertIn("pnl_total", summary)
        self.assertEqual(summary["initial"], 10000)


# ══════════════════════════════════════════════════════════════
#  trading_scorer
# ══════════════════════════════════════════════════════════════

class TestTradingScorer(unittest.TestCase):

    def test_empty_trades_not_ready(self):
        import trading_scorer as ts
        tmpdir = tempfile.mkdtemp()
        orig = ts.TRADES_FILE
        ts.TRADES_FILE = Path(tmpdir) / "empty.json"
        ts.SCORER_FILE = Path(tmpdir) / "report.json"
        report = ts.calculate_metrics()
        self.assertFalse(report["meets_thresholds"])
        ts.TRADES_FILE = orig
        shutil.rmtree(tmpdir)

    def test_metrics_correct(self):
        import trading_scorer as ts
        tmpdir = tempfile.mkdtemp()
        orig_t = ts.TRADES_FILE
        orig_s = ts.SCORER_FILE
        ts.TRADES_FILE = Path(tmpdir) / "trades.json"
        ts.SCORER_FILE = Path(tmpdir) / "report.json"
        trades = [
            {"status": "closed_profit", "pnl_usdt": 100, "pnl_pct": 10, "closed_at": "2026-01-01"},
            {"status": "closed_profit", "pnl_usdt": 50, "pnl_pct": 5, "closed_at": "2026-01-02"},
            {"status": "closed_sl", "pnl_usdt": -30, "pnl_pct": -3, "closed_at": "2026-01-03"},
        ]
        with open(ts.TRADES_FILE, "w") as f:
            json.dump(trades, f)
        with patch("trading_hypothesis.get_accuracy", return_value={"accuracy": 0.7, "total": 10}):
            report = ts.calculate_metrics()
        self.assertEqual(report["total_trades"], 3)
        self.assertAlmostEqual(report["win_rate"], 0.6667, places=2)
        self.assertGreater(report["profit_factor"], 1.0)
        ts.TRADES_FILE = orig_t
        ts.SCORER_FILE = orig_s
        shutil.rmtree(tmpdir)

    def test_scorer_never_activates_phase6(self):
        """Scorer NEVER has code to activate real trading."""
        content = (RUNTIME / "trading_scorer.py").read_text()
        self.assertNotIn("execute_real", content)
        self.assertNotIn("go_live", content)
        self.assertNotIn("activate_phase6", content)

    def test_min_50_trades_required(self):
        import trading_scorer as ts
        self.assertEqual(ts.THRESHOLDS["min_trades"], 50)

    def test_thresholds_defined(self):
        import trading_scorer as ts
        for key in ("win_rate", "profit_factor", "max_drawdown_pct",
                    "hypothesis_accuracy", "min_trades"):
            self.assertIn(key, ts.THRESHOLDS)


if __name__ == "__main__":
    unittest.main()

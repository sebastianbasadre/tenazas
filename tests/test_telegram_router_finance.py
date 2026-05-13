#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


class TestTelegramRouterFinance(unittest.TestCase):
    def test_single_word_binance_is_intercepted(self):
        import telegram_router as tr
        intercept, category = tr.should_intercept("binance")
        self.assertTrue(intercept)
        self.assertEqual(category, "agent:finance")

    def test_execute_intercepted_finance_uses_agent_finance(self):
        import telegram_router as tr

        class FakeFinance:
            @staticmethod
            def handle_query(_text):
                return "Binance: BTC $92,000 (+2.9%)"

        with patch.dict("sys.modules", {"agent_finance": FakeFinance}):
            display, raw = tr.execute_intercepted_skill("binance", category="agent:finance")
        self.assertIn("Binance", display)
        self.assertEqual(display, raw)


if __name__ == "__main__":
    unittest.main()

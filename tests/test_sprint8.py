#!/usr/bin/env python3
"""
test_sprint8.py — Total Copilot tests.

Covers:
  - unified_briefing: complete message, section failure resilience, line limit
  - contextual_awareness: interrupt rules, critical bypass, accumulation
  - personal_optimizer: active hours, minimum data requirement
"""

import json
import os
import shutil
import sys
import tempfile
import time
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
#  unified_briefing
# ══════════════════════════════════════════════════════════════

class TestUnifiedBriefing(unittest.TestCase):

    def _mock_all_sections(self, **overrides):
        """Return patches for all section builders."""
        defaults = {
            "_section_header": "Buenos dias Sebas — Lunes 14/04",
            "_section_calendar": "Agenda hoy\n  09:00 Standup\n  14:00 Review",
            "_section_email": "Emails\n  john@x.com: PR review needed",
            "_section_finance": "Finanzas\n  BTC: +2.3%\n  IOL: $1.2M",
            "_section_homelab": "Homelab: Docker: 20/20 | Ollama: ok | VPN: ES | Disco: 45%",
            "_section_goals": "Goals: todo verde",
            "_section_suggestions": "Sugerencias\n  Investigar sonarr root cause",
            "_section_smoking": "Ayer: 3 cigarrillos",
        }
        defaults.update(overrides)
        return defaults

    def test_full_briefing_all_sections(self):
        import unified_briefing as ub
        mocks = self._mock_all_sections()
        patches = [patch.object(ub, name, return_value=val) for name, val in mocks.items()]
        for p in patches:
            p.start()
        try:
            result = ub.build_briefing()
        finally:
            for p in patches:
                p.stop()
        self.assertGreaterEqual(result["sections_ok"], 3)
        self.assertIn("Buenos dias", result["text"])

    def test_failed_section_omitted(self):
        import unified_briefing as ub
        mocks = self._mock_all_sections()
        mocks["_section_calendar"] = None  # simulate failure
        mocks["_section_email"] = None
        patches = [patch.object(ub, name, return_value=val) for name, val in mocks.items()]
        for p in patches:
            p.start()
        try:
            result = ub.build_briefing()
        finally:
            for p in patches:
                p.stop()
        self.assertNotIn("Agenda", result["text"])
        self.assertIn("Buenos dias", result["text"])  # header still works
        self.assertGreater(result["sections_failed"], 0)

    def test_max_30_lines(self):
        import unified_briefing as ub
        long_finance = "Finanzas\n" + "\n".join(f"  line {i}" for i in range(40))
        mocks = self._mock_all_sections(_section_finance=long_finance)
        patches = [patch.object(ub, name, return_value=val) for name, val in mocks.items()]
        for p in patches:
            p.start()
        try:
            result = ub.build_briefing()
        finally:
            for p in patches:
                p.stop()
        self.assertLessEqual(result["lines"], 30)

    def test_50pct_failure_aborts(self):
        import unified_briefing as ub
        mocks = self._mock_all_sections()
        # Fail 5 of 7 optional sections
        for key in ["_section_calendar", "_section_email", "_section_finance",
                     "_section_suggestions", "_section_smoking"]:
            mocks[key] = None
        patches = [patch.object(ub, name, return_value=val) for name, val in mocks.items()]
        for p in patches:
            p.start()
        try:
            with patch.object(ub, "_notify") as mock_notify:
                result = ub.send_briefing()
        finally:
            for p in patches:
                p.stop()
        self.assertFalse(result["sent"])
        self.assertIn("failures", result.get("reason", ""))

    def test_dry_run_doesnt_send(self):
        import unified_briefing as ub
        mocks = self._mock_all_sections()
        patches = [patch.object(ub, name, return_value=val) for name, val in mocks.items()]
        for p in patches:
            p.start()
        try:
            result = ub.send_briefing(dry_run=True)
        finally:
            for p in patches:
                p.stop()
        self.assertFalse(result["sent"])

    def test_empty_sections_omitted(self):
        """Sections returning None are not in output."""
        import unified_briefing as ub
        mocks = self._mock_all_sections(_section_smoking=None)
        patches = [patch.object(ub, name, return_value=val) for name, val in mocks.items()]
        for p in patches:
            p.start()
        try:
            result = ub.build_briefing()
        finally:
            for p in patches:
                p.stop()
        self.assertNotIn("cigarrillos", result["text"])

    def test_sections_list_defined(self):
        import unified_briefing as ub
        self.assertGreater(len(ub.SECTIONS), 5)


# ══════════════════════════════════════════════════════════════
#  contextual_awareness
# ══════════════════════════════════════════════════════════════

class TestContextualAwareness(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import contextual_awareness as ca
        self._orig_af = ca.ACCUMULATED_FILE
        ca.ACCUMULATED_FILE = Path(self.tmpdir) / "accum.json"
        self.ca = ca

    def tearDown(self):
        self.ca.ACCUMULATED_FILE = self._orig_af
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_critical_always_interrupts(self):
        self.assertTrue(self.ca.can_interrupt("critical"))

    def test_critical_ignores_calendar(self):
        with patch.object(self.ca, "_check_calendar_busy",
                          return_value={"busy": True, "upcoming_minutes": None}):
            self.assertTrue(self.ca.can_interrupt("critical"))

    def test_critical_ignores_absence(self):
        with patch.object(self.ca, "_get_last_telegram_activity", return_value=0):
            self.assertTrue(self.ca.can_interrupt("critical"))

    def test_calendar_busy_blocks_info(self):
        with patch.object(self.ca, "_check_calendar_busy",
                          return_value={"busy": True, "event": "meeting",
                                        "upcoming_minutes": 0}):
            self.assertFalse(self.ca.can_interrupt("info"))

    def test_calendar_busy_blocks_warning(self):
        with patch.object(self.ca, "_check_calendar_busy",
                          return_value={"busy": True, "event": "meeting",
                                        "upcoming_minutes": 0}):
            self.assertFalse(self.ca.can_interrupt("warning"))

    def test_upcoming_event_blocks_info(self):
        with patch.object(self.ca, "_check_calendar_busy",
                          return_value={"busy": False, "upcoming_minutes": 10,
                                        "event": "standup"}):
            self.assertFalse(self.ca.can_interrupt("info"))

    def test_absent_4h_blocks_and_accumulates(self):
        old_ts = time.time() - 5 * 3600  # 5h ago
        with patch.object(self.ca, "_get_last_telegram_activity", return_value=old_ts):
            with patch.object(self.ca, "_check_calendar_busy",
                              return_value={"busy": False, "upcoming_minutes": None}):
                result = self.ca.can_interrupt("info")
        self.assertFalse(result)
        accumulated = self.ca.get_accumulated()
        self.assertGreater(len(accumulated), 0)

    def test_available_allows_info(self):
        recent_ts = time.time() - 60  # 1 min ago
        with patch.object(self.ca, "_get_last_telegram_activity", return_value=recent_ts):
            with patch.object(self.ca, "_check_calendar_busy",
                              return_value={"busy": False, "upcoming_minutes": None}):
                with patch.object(self.ca, "_check_active_critical", return_value=False):
                    try:
                        from temporal_guard import should_notify
                        with patch("temporal_guard.should_notify", return_value=True):
                            result = self.ca.can_interrupt("info")
                    except Exception:
                        result = self.ca.can_interrupt("info")
        self.assertTrue(result)

    def test_clear_accumulated(self):
        self.ca._accumulate_notification("info")
        self.ca._accumulate_notification("warning")
        self.ca.clear_accumulated()
        self.assertEqual(len(self.ca.get_accumulated()), 0)

    def test_get_context_returns_dict(self):
        with patch.object(self.ca, "_check_calendar_busy",
                          return_value={"busy": False, "upcoming_minutes": None}):
            with patch.object(self.ca, "_get_last_telegram_activity", return_value=time.time()):
                with patch.object(self.ca, "_check_active_critical", return_value=False):
                    ctx = self.ca.get_context()
        self.assertIn("calendar_busy", ctx)
        self.assertIn("hours_since_activity", ctx)
        self.assertIn("has_critical", ctx)

    def test_accumulated_capped_at_50(self):
        for _ in range(60):
            self.ca._accumulate_notification("info")
        accumulated = self.ca.get_accumulated()
        self.assertLessEqual(len(accumulated), 50)


# ══════════════════════════════════════════════════════════════
#  personal_optimizer
# ══════════════════════════════════════════════════════════════

class TestPersonalOptimizer(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import personal_optimizer as po
        self._orig_pf = po.PATTERNS_FILE
        self._orig_prefs = po.PREFS_FILE
        self._orig_tl = po.TELEGRAM_LOG
        po.PATTERNS_FILE = Path(self.tmpdir) / "patterns.json"
        po.PREFS_FILE = Path(self.tmpdir) / "prefs.json"
        po.TELEGRAM_LOG = Path(self.tmpdir) / "tg.log"
        self.po = po

    def tearDown(self):
        self.po.PATTERNS_FILE = self._orig_pf
        self.po.PREFS_FILE = self._orig_prefs
        self.po.TELEGRAM_LOG = self._orig_tl
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_telegram_entries(self, count: int, days: int = 14):
        """Write mock Telegram log entries."""
        entries = []
        now = datetime.now().astimezone()
        for i in range(count):
            dt = now - timedelta(days=i % days, hours=i % 12 + 8)
            entries.append(json.dumps({
                "action": "intercepted",
                "category": f"skill:{'weather' if i % 3 == 0 else 'email'}",
                "ts": dt.isoformat(),
            }))
        self.po.TELEGRAM_LOG.write_text("\n".join(entries))

    def test_active_hours_detected(self):
        self._write_telegram_entries(100, days=10)
        result = self.po.detect_active_hours()
        self.assertGreater(len(result["hours"]), 0)
        self.assertGreater(result["total_days"], 0)

    def test_active_hours_empty_logs(self):
        result = self.po.detect_active_hours([])
        self.assertEqual(result["total_days"], 0)
        self.assertFalse(result["sufficient"])

    def test_insufficient_data_no_suggestions(self):
        """Less than 7 days → no suggestions."""
        self._write_telegram_entries(10, days=3)
        report = self.po.analyze()
        # With < 7 days, suggestions should be empty
        if report["active_hours"]["total_days"] < 7:
            self.assertEqual(report["suggestions"], [])

    def test_sufficient_data_has_suggestions(self):
        self._write_telegram_entries(200, days=14)
        report = self.po.analyze()
        if report["active_hours"]["sufficient"]:
            self.assertGreater(len(report["suggestions"]), 0)

    def test_skill_rhythm_detected(self):
        self._write_telegram_entries(100, days=10)
        entries = self.po._load_telegram_activity()
        rhythm = self.po.detect_skill_rhythm(entries)
        self.assertIsInstance(rhythm, dict)

    def test_interruption_tolerance(self):
        self._write_telegram_entries(100, days=10)
        entries = self.po._load_telegram_activity()
        tol = self.po.detect_interruption_tolerance(entries)
        self.assertIn("tolerance_estimate", tol)
        self.assertGreater(tol["tolerance_estimate"], 0)

    def test_decision_patterns(self):
        prefs = {"signals": [
            {"type": "approval", "category": "restart"},
            {"type": "approval", "category": "restart"},
            {"type": "approval", "category": "restart"},
            {"type": "rejection", "category": "cleanup"},
            {"type": "rejection", "category": "cleanup"},
            {"type": "rejection", "category": "cleanup"},
        ]}
        self.po.PREFS_FILE.write_text(json.dumps(prefs))
        result = self.po.detect_decision_patterns()
        self.assertIn("restart", result["fast_approve"])
        self.assertIn("cleanup", result["often_ignored"])

    def test_has_sufficient_data_false(self):
        self.assertFalse(self.po.has_sufficient_data())

    def test_analyze_saves_report(self):
        self._write_telegram_entries(50, days=10)
        self.po.analyze()
        self.assertTrue(self.po.PATTERNS_FILE.exists())

    def test_get_report_empty(self):
        report = self.po.get_report()
        self.assertEqual(report, {})


if __name__ == "__main__":
    unittest.main()

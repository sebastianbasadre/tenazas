#!/usr/bin/env python3
"""
test_sprint2.py — Tests for Sprint 2: HomeLab Operator

Covers:
  - homelab_snapshot.py (payload generation, format, no crashes)
  - homelab_goals.py (goal loading, evaluation, failing goals)
  - temporal_guard.py (time windows, should_notify rules)
  - Integration: proactive_planner uses goals to prioritize
"""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

# Add runtime to path
_CONTAINER_RUNTIME = Path("/home/tenazas/runtime")
_HOST_RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"

if _CONTAINER_RUNTIME.exists() and (_CONTAINER_RUNTIME / "homelab_snapshot.py").exists():
    RUNTIME_DIR = _CONTAINER_RUNTIME
else:
    RUNTIME_DIR = _HOST_RUNTIME
sys.path.insert(0, str(RUNTIME_DIR))

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


# ═══════════════════════════════════════════════════════════════
# HomeLab Snapshot
# ═══════════════════════════════════════════════════════════════

class TestHomelabSnapshot(unittest.TestCase):
    """Test homelab_snapshot generates payload without crashing."""

    def test_import(self):
        import homelab_snapshot
        self.assertTrue(hasattr(homelab_snapshot, 'snapshot'))
        self.assertTrue(hasattr(homelab_snapshot, 'format_telegram'))

    @patch('homelab_snapshot.subprocess')
    @patch('homelab_snapshot.urlopen')
    def test_snapshot_returns_dict(self, mock_urlopen, mock_subprocess):
        """snapshot() returns a dict with all expected keys, even on errors."""
        mock_subprocess.run.return_value = MagicMock(
            returncode=1, stdout="", stderr="")
        mock_urlopen.side_effect = Exception("offline")

        from homelab_snapshot import snapshot
        snap = snapshot()

        self.assertIsInstance(snap, dict)
        self.assertIn("ts", snap)
        self.assertIn("containers", snap)
        self.assertIn("ollama", snap)
        self.assertIn("gpu", snap)
        self.assertIn("ram_cpu", snap)
        self.assertIn("disk", snap)
        self.assertIn("vpn", snap)
        self.assertIn("recent_skills", snap)
        self.assertIn("open_incidents", snap)
        self.assertIn("pending_approvals", snap)

    @patch('homelab_snapshot.subprocess')
    @patch('homelab_snapshot.urlopen')
    def test_snapshot_never_crashes(self, mock_urlopen, mock_subprocess):
        """Even with all collectors failing, snapshot() must not crash."""
        mock_subprocess.run.side_effect = Exception("boom")
        mock_urlopen.side_effect = Exception("boom")

        from homelab_snapshot import snapshot
        snap = snapshot()
        self.assertIsInstance(snap, dict)
        self.assertIn("ts", snap)

    def test_format_telegram_basic(self):
        """format_telegram produces a non-empty string."""
        from homelab_snapshot import format_telegram
        snap = {
            "ts": "2026-04-14T12:00:00-03:00",
            "containers": {"total": 20, "running": 18, "unhealthy": 1, "stopped": 1},
            "ollama": {"status": "online", "models_loaded": ["gemma3:12b"]},
            "gpu": {"temp_c": 45, "load_pct": 10, "vram_used_mb": 8000, "vram_total_mb": 24576},
            "ram_cpu": {"ram_pct": 60, "ram_used_mb": 9800, "ram_total_mb": 16384, "cpu_pct": 15},
            "disk": {"total_gb": 2000, "used_gb": 1200, "free_gb": 800, "used_pct": 60},
            "vpn": {"status": "up", "country": "ES", "ip": "1.2.3.4", "is_es": True},
            "recent_skills": [{"skill": "skill:weather", "ts": "2026-04-14T11:00:00"}],
            "open_incidents": [],
            "pending_approvals": [],
        }
        msg = format_telegram(snap)
        self.assertIsInstance(msg, str)
        self.assertIn("Docker", msg)
        self.assertIn("Ollama", msg)
        self.assertIn("GPU", msg)
        self.assertIn("NAS", msg)
        self.assertIn("VPN", msg)

    def test_format_telegram_with_incidents(self):
        """format_telegram includes incidents and approvals."""
        from homelab_snapshot import format_telegram
        snap = {
            "ts": "2026-04-14T12:00:00-03:00",
            "containers": {"total": 5, "running": 5, "unhealthy": 0, "stopped": 0},
            "ollama": {"status": "online", "models_loaded": []},
            "gpu": {"error": "unavailable"},
            "ram_cpu": {"ram_pct": 50, "ram_used_mb": 8000, "ram_total_mb": 16000, "cpu_pct": 10},
            "disk": {"total_gb": 2000, "used_gb": 1000, "free_gb": 1000, "used_pct": 50},
            "vpn": {"status": "up", "country": "ES", "ip": "1.2.3.4", "is_es": True},
            "recent_skills": [],
            "open_incidents": [
                {"id": "abc123", "title": "Sonarr down", "severity": "critical", "status": "detected"}
            ],
            "pending_approvals": [
                {"id": "def456", "action": "restart_container", "target": "sonarr", "risk": "low"}
            ],
        }
        msg = format_telegram(snap)
        self.assertIn("Incidentes abiertos", msg)
        self.assertIn("Sonarr down", msg)
        self.assertIn("Aprobaciones pendientes", msg)
        self.assertIn("restart_container", msg)

    @patch('homelab_snapshot.subprocess')
    @patch('homelab_snapshot.urlopen')
    def test_collect_containers_format(self, mock_urlopen, mock_subprocess):
        """_collect_containers returns proper structure."""
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout="sonarr\tUp 3 hours\nradarr\tUp 3 hours\ngluetun\tExited (1) 5 min ago\n",
            stderr="",
        )
        from homelab_snapshot import _collect_containers
        result = _collect_containers()
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["running"], 2)
        self.assertEqual(result["stopped"], 1)

    def test_collect_pending_approvals_empty(self):
        """_collect_pending_approvals handles missing file."""
        from homelab_snapshot import _collect_pending_approvals
        with patch.object(Path, 'exists', return_value=False):
            result = _collect_pending_approvals()
        self.assertEqual(result, [])

    def test_collect_open_incidents_no_dir(self):
        """_collect_open_incidents handles missing directory."""
        from homelab_snapshot import _collect_open_incidents
        result = _collect_open_incidents()
        self.assertIsInstance(result, list)


# ═══════════════════════════════════════════════════════════════
# HomeLab Goals
# ═══════════════════════════════════════════════════════════════

class TestHomelabGoals(unittest.TestCase):
    """Test homelab_goals evaluation and persistence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.goals_path = Path(self.tmpdir) / "homelab_goals.json"

    def tearDown(self):
        if self.goals_path.exists():
            self.goals_path.unlink()
        os.rmdir(self.tmpdir)

    def test_import(self):
        import homelab_goals
        self.assertTrue(hasattr(homelab_goals, 'evaluate_all'))
        self.assertTrue(hasattr(homelab_goals, 'get_failing_goals'))
        self.assertTrue(hasattr(homelab_goals, 'get_all_goals'))

    def test_default_goals_count(self):
        from homelab_goals import DEFAULT_GOALS
        self.assertEqual(len(DEFAULT_GOALS), 6)

    def test_default_goals_ids(self):
        from homelab_goals import DEFAULT_GOALS
        ids = {g["id"] for g in DEFAULT_GOALS}
        self.assertIn("ollama_available", ids)
        self.assertIn("gemma_warm", ids)
        self.assertIn("ram_healthy", ids)
        self.assertIn("vpn_healthy", ids)
        self.assertIn("containers_healthy", ids)
        self.assertIn("router_accuracy", ids)

    def test_default_goals_priorities(self):
        from homelab_goals import DEFAULT_GOALS
        priorities = [g["priority"] for g in DEFAULT_GOALS]
        self.assertEqual(sorted(priorities), [1, 2, 3, 4, 5, 6])

    @patch('homelab_goals.GOALS_PATH')
    def test_load_goals_initializes_defaults(self, mock_path):
        mock_path.exists.return_value = False
        mock_path.parent.mkdir = MagicMock()
        mock_path.write_text = MagicMock()

        from homelab_goals import _load_goals
        goals = _load_goals()
        self.assertEqual(len(goals), 6)
        for g in goals:
            self.assertIn("status", g)
            self.assertIn("last_checked", g)
            self.assertIn("evidence", g)

    @patch('homelab_goals.urlopen')
    def test_eval_ollama_available_ok(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"models": [{"name": "gemma3:12b"}]}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from homelab_goals import _eval_ollama_available
        status, evidence = _eval_ollama_available()
        self.assertEqual(status, "ok")
        self.assertIn("online", evidence)

    @patch('homelab_goals.urlopen')
    def test_eval_ollama_available_fail(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("connection refused")

        from homelab_goals import _eval_ollama_available
        status, evidence = _eval_ollama_available()
        self.assertEqual(status, "fail")
        self.assertIn("offline", evidence)

    @patch('homelab_goals.urlopen')
    def test_eval_gemma_warm_ok(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "models": [{"name": "gemma3:12b", "size_vram": 8074129571}]
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from homelab_goals import _eval_gemma_warm
        status, evidence = _eval_gemma_warm()
        self.assertEqual(status, "ok")

    @patch('homelab_goals.urlopen')
    def test_eval_gemma_warm_missing(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "models": [{"name": "qwen3:8b"}]
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from homelab_goals import _eval_gemma_warm
        status, evidence = _eval_gemma_warm()
        self.assertEqual(status, "warn")
        self.assertIn("not in VRAM", evidence)

    @patch('homelab_goals.subprocess')
    def test_eval_ram_healthy_ok(self, mock_subprocess):
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout="MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\n",
        )
        from homelab_goals import _eval_ram_healthy
        status, evidence = _eval_ram_healthy()
        self.assertEqual(status, "ok")
        self.assertIn("50%", evidence)

    @patch('homelab_goals.subprocess')
    def test_eval_ram_healthy_fail(self, mock_subprocess):
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout="MemTotal:       16384000 kB\nMemAvailable:    1638400 kB\n",
        )
        from homelab_goals import _eval_ram_healthy
        status, evidence = _eval_ram_healthy()
        self.assertEqual(status, "fail")
        self.assertIn("90%", evidence)

    @patch('homelab_goals.subprocess')
    @patch('homelab_goals.urlopen')
    def test_eval_vpn_healthy_ok(self, mock_urlopen, mock_subprocess):
        mock_subprocess.run.return_value = MagicMock(
            returncode=0, stdout="Up 3 hours\n")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"country": "ES"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from homelab_goals import _eval_vpn_healthy
        status, evidence = _eval_vpn_healthy()
        self.assertEqual(status, "ok")
        self.assertIn("ES", evidence)

    @patch('homelab_goals.subprocess')
    def test_eval_vpn_down(self, mock_subprocess):
        mock_subprocess.run.return_value = MagicMock(
            returncode=0, stdout="Exited (1) 5 min ago\n")

        from homelab_goals import _eval_vpn_healthy
        status, evidence = _eval_vpn_healthy()
        self.assertEqual(status, "fail")
        self.assertIn("not running", evidence)

    @patch('homelab_goals.subprocess')
    def test_eval_containers_healthy_ok(self, mock_subprocess):
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout="sonarr\tUp 3 hours\nradarr\tUp 3 hours\n",
        )
        from homelab_goals import _eval_containers_healthy
        status, evidence = _eval_containers_healthy()
        self.assertEqual(status, "ok")

    @patch('homelab_goals.subprocess')
    def test_eval_containers_unhealthy(self, mock_subprocess):
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout="sonarr\tUp 3 hours (unhealthy)\nradarr\tUp 3 hours\n",
        )
        from homelab_goals import _eval_containers_healthy
        status, evidence = _eval_containers_healthy()
        self.assertEqual(status, "fail")
        self.assertIn("sonarr", evidence)

    @patch('homelab_goals.subprocess')
    def test_eval_containers_ignores_hireswarm(self, mock_subprocess):
        mock_subprocess.run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "hireswarm-inngest\tUp 3 hours (unhealthy)\n"
                "hireswarm-agent-server\tUp 3 hours (unhealthy)\n"
                "sonarr\tUp 3 hours\n"
            ),
        )
        from homelab_goals import _eval_containers_healthy
        status, evidence = _eval_containers_healthy()
        self.assertEqual(status, "ok")
        self.assertIn("2 ignored", evidence)

    def test_get_failing_goals_filters(self):
        """get_failing_goals only returns warn/fail goals."""
        from homelab_goals import get_failing_goals
        mock_goals = [
            {"id": "a", "status": "ok", "priority": 1},
            {"id": "b", "status": "fail", "priority": 2},
            {"id": "c", "status": "warn", "priority": 3},
        ]
        with patch('homelab_goals._load_goals', return_value=mock_goals):
            failing = get_failing_goals()
        self.assertEqual(len(failing), 2)
        self.assertEqual(failing[0]["id"], "b")  # sorted by priority

    def test_format_status(self):
        from homelab_goals import format_status
        goals = [
            {"id": "a", "desc": "Test goal", "priority": 1, "status": "ok", "evidence": "all good"},
            {"id": "b", "desc": "Another", "priority": 2, "status": "fail", "evidence": "broken"},
        ]
        text = format_status(goals)
        self.assertIn("[OK]", text)
        self.assertIn("[XX]", text)
        self.assertIn("1/2 goals met", text)


# ═══════════════════════════════════════════════════════════════
# Temporal Guard
# ═══════════════════════════════════════════════════════════════

class TestTemporalGuard(unittest.TestCase):
    """Test temporal_guard time window filtering."""

    def test_import(self):
        import temporal_guard
        self.assertTrue(hasattr(temporal_guard, 'should_notify'))
        self.assertTrue(hasattr(temporal_guard, 'current_window'))
        self.assertTrue(hasattr(temporal_guard, 'should_allow_action'))

    def test_current_window_night(self):
        from temporal_guard import current_window
        for hour in (0, 1, 3, 5, 6):
            now = datetime(2026, 4, 14, hour, 0, tzinfo=TZ)
            self.assertEqual(current_window(now), "night", f"hour={hour}")

    def test_current_window_morning(self):
        from temporal_guard import current_window
        for hour in (7, 8, 9, 10, 11):
            now = datetime(2026, 4, 14, hour, 0, tzinfo=TZ)
            self.assertEqual(current_window(now), "morning", f"hour={hour}")

    def test_current_window_work(self):
        from temporal_guard import current_window
        for hour in (12, 13, 15, 17, 19):
            now = datetime(2026, 4, 14, hour, 0, tzinfo=TZ)
            self.assertEqual(current_window(now), "work", f"hour={hour}")

    def test_current_window_evening(self):
        from temporal_guard import current_window
        for hour in (20, 21, 22, 23):
            now = datetime(2026, 4, 14, hour, 0, tzinfo=TZ)
            self.assertEqual(current_window(now), "evening", f"hour={hour}")

    # ── Night rules ──

    def test_night_critical_passes(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 3, 0, tzinfo=TZ)
        self.assertTrue(should_notify("critical", "infra", now=now, skip_calendar_check=True))

    def test_night_warning_blocked(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 3, 0, tzinfo=TZ)
        self.assertFalse(should_notify("warning", "infra", now=now, skip_calendar_check=True))

    def test_night_info_blocked(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 3, 0, tzinfo=TZ)
        self.assertFalse(should_notify("info", "calendar", now=now, skip_calendar_check=True))

    def test_night_suggestion_blocked(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 3, 0, tzinfo=TZ)
        self.assertFalse(should_notify("info", "suggestion", now=now, skip_calendar_check=True))

    # ── Morning rules ──

    def test_morning_calendar_passes(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 8, 0, tzinfo=TZ)
        self.assertTrue(should_notify("info", "calendar", now=now, skip_calendar_check=True))

    def test_morning_finance_passes(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 8, 0, tzinfo=TZ)
        self.assertTrue(should_notify("info", "finance", now=now, skip_calendar_check=True))

    def test_morning_infra_warning_passes(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 8, 0, tzinfo=TZ)
        self.assertTrue(should_notify("warning", "infra", now=now, skip_calendar_check=True))

    def test_morning_suggestion_blocked(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 8, 0, tzinfo=TZ)
        self.assertFalse(should_notify("info", "suggestion", now=now, skip_calendar_check=True))

    # ── Work rules ──

    def test_work_everything_passes(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 14, 0, tzinfo=TZ)
        self.assertTrue(should_notify("info", "suggestion", now=now, skip_calendar_check=True))
        self.assertTrue(should_notify("warning", "infra", now=now, skip_calendar_check=True))
        self.assertTrue(should_notify("info", "calendar", now=now, skip_calendar_check=True))

    @patch('temporal_guard._has_upcoming_event', return_value=True)
    def test_work_upcoming_event_blocks_info(self, mock_event):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 14, 0, tzinfo=TZ)
        self.assertFalse(should_notify("info", "suggestion", now=now, skip_calendar_check=False))

    @patch('temporal_guard._has_upcoming_event', return_value=True)
    def test_work_upcoming_event_allows_critical(self, mock_event):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 14, 0, tzinfo=TZ)
        self.assertTrue(should_notify("critical", "infra", now=now, skip_calendar_check=False))

    # ── Evening rules ──

    def test_evening_infra_warning_blocked(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 21, 0, tzinfo=TZ)
        self.assertFalse(should_notify("warning", "infra", now=now, skip_calendar_check=True))

    def test_evening_critical_passes(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 21, 0, tzinfo=TZ)
        self.assertTrue(should_notify("critical", "infra", now=now, skip_calendar_check=True))

    def test_evening_info_suggestion_blocked(self):
        from temporal_guard import should_notify
        now = datetime(2026, 4, 14, 21, 0, tzinfo=TZ)
        self.assertFalse(should_notify("info", "suggestion", now=now, skip_calendar_check=True))

    # ── Approval always passes ──

    def test_approval_always_passes(self):
        from temporal_guard import should_notify
        for hour in (3, 8, 14, 22):
            now = datetime(2026, 4, 14, hour, 0, tzinfo=TZ)
            self.assertTrue(should_notify("info", "approval", now=now, skip_calendar_check=True),
                            f"Approval should pass at hour={hour}")

    # ── Action guard ──

    def test_evening_no_restarts(self):
        from temporal_guard import should_allow_action
        now = datetime(2026, 4, 14, 21, 0, tzinfo=TZ)
        self.assertFalse(should_allow_action("restart_container", severity="warning", now=now))

    def test_evening_restart_ok_if_critical(self):
        from temporal_guard import should_allow_action
        now = datetime(2026, 4, 14, 21, 0, tzinfo=TZ)
        self.assertTrue(should_allow_action("restart_container", severity="critical", now=now))

    def test_night_non_critical_blocked(self):
        from temporal_guard import should_allow_action
        now = datetime(2026, 4, 14, 3, 0, tzinfo=TZ)
        self.assertFalse(should_allow_action("health_check", severity="info", now=now))

    def test_work_everything_allowed(self):
        from temporal_guard import should_allow_action
        now = datetime(2026, 4, 14, 14, 0, tzinfo=TZ)
        self.assertTrue(should_allow_action("restart_container", severity="warning", now=now))

    # ── Context ──

    def test_get_context(self):
        from temporal_guard import get_context
        now = datetime(2026, 4, 14, 14, 0, tzinfo=TZ)
        ctx = get_context(now)
        self.assertEqual(ctx["window"], "work")
        self.assertEqual(ctx["hour"], 14)
        self.assertEqual(ctx["day_name"], "Martes")
        self.assertFalse(ctx["is_weekend"])


# ═══════════════════════════════════════════════════════════════
# Integration: Planner + Goals
# ═══════════════════════════════════════════════════════════════

class TestPlannerGoalsIntegration(unittest.TestCase):
    """Test that proactive_planner uses homelab_goals."""

    def test_check_goals_returns_tasks(self):
        """_check_goals generates tasks from failing goals."""
        # Import the function from proactive_planner
        mock_failing = [
            {"id": "ollama_available", "desc": "Ollama siempre disponible",
             "priority": 1, "status": "fail", "evidence": "Ollama offline"},
            {"id": "ram_healthy", "desc": "RAM < 85%",
             "priority": 3, "status": "warn", "evidence": "RAM at 88%"},
        ]
        with patch.dict(sys.modules, {}):
            # Need to patch at the source
            with patch('homelab_goals.evaluate_all', return_value=mock_failing):
                from proactive_planner import _check_goals
                tasks = _check_goals()

        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["type"], "goal")
        self.assertEqual(tasks[0]["priority"], "high")  # fail → high
        self.assertEqual(tasks[0]["goal_id"], "ollama_available")
        self.assertEqual(tasks[1]["priority"], "medium")  # warn → medium

    def test_check_goals_handles_import_error(self):
        """_check_goals returns empty list if homelab_goals not available."""
        with patch.dict(sys.modules, {'homelab_goals': None}):
            from proactive_planner import _check_goals
            # Force ImportError by making the import fail
            with patch('builtins.__import__', side_effect=ImportError("no module")):
                tasks = _check_goals()
        self.assertEqual(tasks, [])


# ═══════════════════════════════════════════════════════════════
# Integration: Temporal Guard + Planner
# ═══════════════════════════════════════════════════════════════

class TestTemporalGuardIntegration(unittest.TestCase):
    """Test temporal_guard integration with proactive_planner."""

    def test_planner_imports_temporal_guard(self):
        """proactive_planner.py references temporal_guard."""
        planner_path = RUNTIME_DIR / "proactive_planner.py"
        content = planner_path.read_text()
        self.assertIn("temporal_guard", content)
        self.assertIn("should_notify", content)

    def test_notification_poller_imports_temporal_guard(self):
        """notification_poller.py references temporal_guard."""
        poller_path = RUNTIME_DIR / "notification_poller.py"
        content = poller_path.read_text()
        self.assertIn("temporal_guard", content)


# ═══════════════════════════════════════════════════════════════
# Integration: Telegram Router + Approval Queue
# ═══════════════════════════════════════════════════════════════

class TestTelegramApprovalIntegration(unittest.TestCase):
    """Test telegram_router handles approval commands."""

    def test_router_handles_aq_callbacks(self):
        """telegram_router.py handles aq_ prefixed callbacks."""
        router_path = RUNTIME_DIR / "telegram_router.py"
        content = router_path.read_text()
        self.assertIn("aq_", content)
        self.assertIn("approval_queue", content)

    def test_router_handles_estado_homelab(self):
        """telegram_router.py handles 'estado homelab' text command."""
        router_path = RUNTIME_DIR / "telegram_router.py"
        content = router_path.read_text()
        self.assertIn("estado homelab", content)
        self.assertIn("homelab_snapshot", content)

    def test_router_handles_aprobar_command(self):
        """telegram_router.py handles 'aprobar' text command."""
        router_path = RUNTIME_DIR / "telegram_router.py"
        content = router_path.read_text()
        self.assertIn("aprobar", content)
        self.assertIn("approve_by_text", content)

    def test_router_handles_rechazar_command(self):
        """telegram_router.py handles 'rechazar' text command."""
        router_path = RUNTIME_DIR / "telegram_router.py"
        content = router_path.read_text()
        self.assertIn("rechazar", content)


# ═══════════════════════════════════════════════════════════════
# Network Exporter
# ═══════════════════════════════════════════════════════════════

class TestNetworkExporter(unittest.TestCase):
    """Test network_exporter.py exists and has required metrics."""

    def test_exporter_exists(self):
        exporter_path = RUNTIME_DIR / "network_exporter.py"
        self.assertTrue(exporter_path.exists())

    def test_exporter_has_required_metrics(self):
        exporter_path = RUNTIME_DIR / "network_exporter.py"
        content = exporter_path.read_text()
        self.assertIn("tenazas_devices_total", content)
        self.assertIn("tenazas_devices_online", content)
        self.assertIn("tenazas_devices_missing", content)
        self.assertIn("tenazas_devices_classified", content)
        self.assertIn("tenazas_device_info", content)

    def test_prometheus_config_has_scrape(self):
        prom_path = Path(__file__).parent.parent / "prometheus.yml"
        if prom_path.exists():
            content = prom_path.read_text()
            self.assertIn("9108", content)
            self.assertIn("tenazas_network", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)

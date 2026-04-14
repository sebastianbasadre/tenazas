#!/usr/bin/env python3
"""
test_sprint1.py — Tests for Sprint 1: Safe Operational Foundation

Covers:
  - incident_schema.json validation
  - action_registry.py (registry, can_execute, whitelists)
  - policy_engine.py (risk evaluation, cooldowns, safety_guard integration)
  - approval_queue.py (enqueue, dedup, expiration, status)
  - action_validator.py (post-execution validators)
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add runtime to path — works both on host and inside container
# Container: runtime is volume-mounted at /home/tenazas/runtime
# Host: relative to this file at ../workspace/runtime
_CONTAINER_RUNTIME = Path("/home/tenazas/runtime")
_HOST_RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"

if _CONTAINER_RUNTIME.exists() and (_CONTAINER_RUNTIME / "action_registry.py").exists():
    RUNTIME_DIR = _CONTAINER_RUNTIME
else:
    RUNTIME_DIR = _HOST_RUNTIME
sys.path.insert(0, str(RUNTIME_DIR))


# ═══════════════════════════════════════════════════════════════
# Incident Schema
# ═══════════════════════════════════════════════════════════════

class TestIncidentSchema(unittest.TestCase):
    """Validate incident_schema.json is well-formed."""

    def setUp(self):
        schema_path = RUNTIME_DIR / "schemas" / "incident_schema.json"
        with open(schema_path) as f:
            self.schema = json.load(f)

    def test_schema_has_required_fields(self):
        required = self.schema["required"]
        self.assertIn("incident_id", required)
        self.assertIn("source", required)
        self.assertIn("category", required)
        self.assertIn("severity", required)
        self.assertIn("title", required)
        self.assertIn("status", required)

    def test_schema_has_all_properties(self):
        props = self.schema["properties"]
        expected = ["incident_id", "source", "category", "severity", "title",
                    "evidence", "probable_cause", "recommended_action",
                    "requires_approval", "confidence", "rollback", "status",
                    "detected_at", "resolved_at", "approval_id"]
        for field in expected:
            self.assertIn(field, props, f"Missing property: {field}")

    def test_severity_enum(self):
        sevs = self.schema["properties"]["severity"]["enum"]
        self.assertEqual(sevs, ["critical", "warning", "info"])

    def test_status_enum(self):
        statuses = self.schema["properties"]["status"]["enum"]
        self.assertIn("detected", statuses)
        self.assertIn("pending_approval", statuses)
        self.assertIn("approved", statuses)
        self.assertIn("resolved", statuses)
        self.assertIn("expired", statuses)

    def test_confidence_range(self):
        conf = self.schema["properties"]["confidence"]
        self.assertEqual(conf["minimum"], 0)
        self.assertEqual(conf["maximum"], 1)

    def test_no_additional_properties(self):
        self.assertFalse(self.schema["additionalProperties"])

    def test_category_enum_has_expected_values(self):
        cats = self.schema["properties"]["category"]["enum"]
        for expected in ["container_down", "vpn_down", "vpn_leak",
                         "ollama_offline", "network_down"]:
            self.assertIn(expected, cats)


# ═══════════════════════════════════════════════════════════════
# Action Registry
# ═══════════════════════════════════════════════════════════════

class TestActionRegistry(unittest.TestCase):
    """Test action_registry.py — single source of truth for actions."""

    def setUp(self):
        import action_registry as ar
        self.ar = ar

    def test_get_known_action(self):
        action = self.ar.get_action("restart_container")
        self.assertIsNotNone(action)
        self.assertEqual(action["category"], "docker")

    def test_get_unknown_action(self):
        self.assertIsNone(self.ar.get_action("destroy_everything"))

    def test_list_all_actions(self):
        actions = self.ar.list_actions()
        self.assertGreater(len(actions), 5)

    def test_list_by_category(self):
        docker_actions = self.ar.list_actions(category="docker")
        for a in docker_actions:
            self.assertEqual(a["category"], "docker")

    def test_list_by_risk(self):
        safe_actions = self.ar.list_actions(risk_level="safe")
        for a in safe_actions:
            self.assertEqual(a["risk_level"], "safe")

    def test_can_execute_arr_container(self):
        result = self.ar.can_execute("restart_container", "sonarr")
        self.assertTrue(result["allowed"])

    def test_cannot_execute_openclaw(self):
        """openclaw is ALWAYS blocked — inherited from docker_wrapper."""
        result = self.ar.can_execute("restart_container", "openclaw")
        self.assertFalse(result["allowed"])
        self.assertIn("blocked", result["reason"].lower())

    def test_cannot_execute_watchtower(self):
        """watchtower is ALWAYS blocked — inherited from safety_guard."""
        result = self.ar.can_execute("restart_container", "watchtower")
        self.assertFalse(result["allowed"])

    def test_unknown_action_blocked(self):
        result = self.ar.can_execute("delete_all_data", "everything")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["risk_level"], "high")

    def test_unknown_container_blocked(self):
        """Container not in whitelist = blocked."""
        result = self.ar.can_execute("restart_container", "random_container")
        self.assertFalse(result["allowed"])

    def test_safe_action_no_approval(self):
        result = self.ar.can_execute("health_check")
        self.assertTrue(result["allowed"])
        self.assertFalse(result["requires_approval"])

    def test_stop_requires_approval(self):
        action = self.ar.get_action("stop_container")
        self.assertTrue(action["requires_approval"])

    def test_all_actions_have_required_fields(self):
        required_fields = ["name", "category", "risk_level", "requires_approval",
                           "cooldown_seconds", "max_retries"]
        for action in self.ar.list_actions():
            for field in required_fields:
                self.assertIn(field, action, f"Action {action['name']} missing {field}")

    def test_imports_from_docker_wrapper(self):
        """Verify registry uses docker_wrapper's blocked containers."""
        from docker_wrapper import BLOCKED_CONTAINERS
        action = self.ar.get_action("restart_container")
        for blocked in BLOCKED_CONTAINERS:
            self.assertIn(blocked, action["blocked_targets"])

    def test_imports_from_safety_guard(self):
        """Verify registry uses safety_guard's protected containers."""
        from safety_guard import PROTECTED_CONTAINERS
        action = self.ar.get_action("restart_container")
        for protected in PROTECTED_CONTAINERS:
            self.assertIn(protected, action["blocked_targets"])

    def test_cooldown_returns_value(self):
        cd = self.ar.get_cooldown("restart_container")
        self.assertEqual(cd, 1200)

    def test_cooldown_unknown_returns_zero(self):
        cd = self.ar.get_cooldown("nonexistent")
        self.assertEqual(cd, 0)

    def test_rollback_defined(self):
        rb = self.ar.get_rollback("restart_container")
        self.assertIsNotNone(rb)
        self.assertIn("action", rb)

    def test_rollback_unknown_returns_none(self):
        self.assertIsNone(self.ar.get_rollback("nonexistent"))

    def test_allowed_targets_for_ollama(self):
        action = self.ar.get_action("reload_model")
        self.assertIn("gemma3:12b", action["allowed_targets"])
        self.assertIn("qwen3:8b", action["allowed_targets"])

    def test_can_execute_ollama_model(self):
        result = self.ar.can_execute("reload_model", "gemma3:12b")
        self.assertTrue(result["allowed"])

    def test_cannot_reload_unknown_model(self):
        result = self.ar.can_execute("reload_model", "gpt-4")
        self.assertFalse(result["allowed"])


# ═══════════════════════════════════════════════════════════════
# Policy Engine
# ═══════════════════════════════════════════════════════════════

class TestPolicyEngine(unittest.TestCase):
    """Test policy_engine.py — risk-aware execution decisions."""

    def setUp(self):
        import policy_engine as pe
        self.pe = pe
        # Use temp file for cooldown state
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        pe.COOLDOWN_STATE_PATH = Path(self.tmp.name)

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    def test_safe_action_auto_executes(self):
        decision = self.pe.evaluate("health_check")
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.execute_now)
        self.assertFalse(decision.requires_approval)
        self.assertEqual(decision.risk_level, "safe")

    def test_low_risk_auto_executes(self):
        decision = self.pe.evaluate("restart_container", "sonarr")
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.execute_now)

    def test_low_risk_blocked_by_cooldown(self):
        """After execution, cooldown prevents immediate re-execution."""
        self.pe.record_execution("restart_container", "sonarr")
        decision = self.pe.evaluate("restart_container", "sonarr")
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.execute_now)
        self.assertGreater(decision.cooldown_remaining, 0)

    def test_medium_risk_needs_approval(self):
        decision = self.pe.evaluate("stop_container", "sonarr")
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.execute_now)
        self.assertTrue(decision.requires_approval)

    def test_high_risk_always_needs_approval(self):
        decision = self.pe.evaluate("cleanup_disk", confidence=0.99)
        self.assertTrue(decision.requires_approval)
        self.assertFalse(decision.execute_now)

    def test_unknown_action_blocked(self):
        decision = self.pe.evaluate("format_disk", "volume1")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk_level, "high")

    def test_openclaw_blocked_by_safety_guard(self):
        """openclaw protection inherited from safety_guard."""
        decision = self.pe.evaluate("restart_container", "openclaw")
        self.assertFalse(decision.allowed)

    def test_watchtower_blocked(self):
        decision = self.pe.evaluate("restart_container", "watchtower")
        self.assertFalse(decision.allowed)

    def test_policy_decision_has_all_fields(self):
        decision = self.pe.evaluate("health_check")
        self.assertIsInstance(decision.action_name, str)
        self.assertIsInstance(decision.allowed, bool)
        self.assertIsInstance(decision.execute_now, bool)
        self.assertIsInstance(decision.requires_approval, bool)
        self.assertIsInstance(decision.risk_level, str)
        self.assertIsInstance(decision.reason, str)

    def test_record_execution_enables_cooldown(self):
        self.pe.record_execution("restart_vpn", "gluetun")
        remaining = self.pe._check_cooldown("restart_vpn", "gluetun")
        self.assertGreater(remaining, 0)

    def test_evaluate_incident(self):
        incident = {
            "recommended_action": {
                "action_name": "restart_container",
                "target": "sonarr",
            },
            "confidence": 0.85,
            "title": "sonarr is down",
        }
        decision = self.pe.evaluate_incident(incident)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.action_name, "restart_container")

    def test_evaluate_incident_no_action(self):
        incident = {"title": "something bad"}
        decision = self.pe.evaluate_incident(incident)
        self.assertFalse(decision.allowed)

    def test_safety_guard_integration(self):
        """Verify that safety_guard.validate_action is called for docker ops."""
        with patch("policy_engine.validate_action") as mock_sg:
            mock_sg.return_value = {"allowed": False, "reason": "blocked by guard"}
            decision = self.pe.evaluate("restart_container", "sonarr")
            mock_sg.assert_called_once()
            self.assertFalse(decision.allowed)
            self.assertIn("safety_guard", decision.reason)


# ═══════════════════════════════════════════════════════════════
# Approval Queue
# ═══════════════════════════════════════════════════════════════

class TestApprovalQueue(unittest.TestCase):
    """Test approval_queue.py — queue with Telegram buttons."""

    def setUp(self):
        import approval_queue as aq
        self.aq = aq
        # Use temp file for queue
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        aq.QUEUE_PATH = Path(self.tmp.name)
        Path(self.tmp.name).write_text("[]")

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    @patch.object(sys.modules.get("approval_queue", MagicMock()),
                  "_send_approval_message", return_value=None)
    def test_enqueue_creates_entry(self, _mock):
        self.aq._send_approval_message = lambda e: None  # skip Telegram
        aid = self.aq.enqueue("restart_container", "sonarr", context="test")
        self.assertIsInstance(aid, str)
        self.assertEqual(len(aid), 12)

    def test_enqueue_dedup(self):
        """Same action+target while pending = reuse existing."""
        self.aq._send_approval_message = lambda e: None
        aid1 = self.aq.enqueue("restart_container", "sonarr")
        aid2 = self.aq.enqueue("restart_container", "sonarr")
        self.assertEqual(aid1, aid2)

    def test_enqueue_different_targets(self):
        self.aq._send_approval_message = lambda e: None
        aid1 = self.aq.enqueue("restart_container", "sonarr")
        aid2 = self.aq.enqueue("restart_container", "radarr")
        self.assertNotEqual(aid1, aid2)

    def test_check_status_pending(self):
        self.aq._send_approval_message = lambda e: None
        aid = self.aq.enqueue("restart_container", "sonarr")
        status = self.aq.check_status(aid)
        self.assertEqual(status, "pending")

    def test_check_status_not_found(self):
        self.assertEqual(self.aq.check_status("nonexistent"), "not_found")

    def test_expiration(self):
        """Expired entries detected by check_status."""
        self.aq._send_approval_message = lambda e: None
        aid = self.aq.enqueue("restart_container", "sonarr")
        # Manually expire it
        queue = self.aq._load_queue()
        for e in queue:
            if e["id"] == aid:
                e["expires_at"] = time.time() - 1
        self.aq._save_queue(queue)
        self.assertEqual(self.aq.check_status(aid), "expired")

    def test_get_pending(self):
        self.aq._send_approval_message = lambda e: None
        self.aq.enqueue("restart_container", "sonarr")
        self.aq.enqueue("restart_container", "radarr")
        pending = self.aq.get_pending()
        self.assertEqual(len(pending), 2)

    def test_approve_by_text(self):
        self.aq._send_approval_message = lambda e: None
        aid = self.aq.enqueue("restart_container", "sonarr")
        result = self.aq.approve_by_text("sonarr")
        self.assertEqual(result, aid)
        self.assertEqual(self.aq.check_status(aid), "approved")

    def test_approve_by_text_no_match(self):
        self.aq._send_approval_message = lambda e: None
        self.aq.enqueue("restart_container", "sonarr")
        self.assertIsNone(self.aq.approve_by_text("nonexistent"))

    def test_handle_callback_approve(self):
        self.aq._send_approval_message = lambda e: None
        aid = self.aq.enqueue("restart_container", "sonarr")

        callback = {
            "id": "cb123",
            "data": f"aq_approve:{aid}",
            "message": {"chat": {"id": "123"}, "message_id": "456"},
        }
        with patch.object(self.aq, "_tg_request", return_value={"ok": True}):
            with patch.object(self.aq, "_load_bot_token", return_value="fake"):
                result = self.aq.handle_callback(callback)
        self.assertEqual(result["action"], "approved")
        self.assertEqual(self.aq.check_status(aid), "approved")

    def test_handle_callback_reject(self):
        self.aq._send_approval_message = lambda e: None
        aid = self.aq.enqueue("restart_container", "sonarr")

        callback = {
            "id": "cb123",
            "data": f"aq_reject:{aid}",
            "message": {"chat": {"id": "123"}, "message_id": "456"},
        }
        with patch.object(self.aq, "_tg_request", return_value={"ok": True}):
            with patch.object(self.aq, "_load_bot_token", return_value="fake"):
                result = self.aq.handle_callback(callback)
        self.assertEqual(result["action"], "rejected")

    def test_handle_callback_postpone(self):
        self.aq._send_approval_message = lambda e: None
        aid = self.aq.enqueue("restart_container", "sonarr")

        callback = {
            "id": "cb123",
            "data": f"aq_postpone:{aid}",
            "message": {"chat": {"id": "123"}, "message_id": "456"},
        }
        with patch.object(self.aq, "_tg_request", return_value={"ok": True}):
            with patch.object(self.aq, "_load_bot_token", return_value="fake"):
                result = self.aq.handle_callback(callback)
        self.assertEqual(result["action"], "postponed")

    def test_queue_max_size(self):
        self.aq._send_approval_message = lambda e: None
        for i in range(60):
            # Use unique targets to avoid dedup
            self.aq.enqueue("health_check", f"target_{i}")
        queue = self.aq._load_queue()
        self.assertLessEqual(len(queue), self.aq.MAX_QUEUE_SIZE)


# ═══════════════════════════════════════════════════════════════
# Action Validator
# ═══════════════════════════════════════════════════════════════

class TestActionValidator(unittest.TestCase):
    """Test action_validator.py — post-execution validation."""

    def setUp(self):
        import action_validator as av
        self.av = av

    @patch("action_validator.subprocess.run")
    @patch("action_validator.time.sleep")
    def test_restart_container_success(self, mock_sleep, mock_run):
        mock_run.return_value = MagicMock(
            stdout="sonarr\tUp 5 minutes", stderr="", returncode=0)
        result = self.av.validate_restart_container("sonarr")
        self.assertTrue(result.success)
        self.assertIn("Up", result.reason)

    @patch("action_validator.subprocess.run")
    @patch("action_validator.time.sleep")
    def test_restart_container_still_down(self, mock_sleep, mock_run):
        mock_run.return_value = MagicMock(
            stdout="sonarr\tExited (1) 2 minutes ago", stderr="", returncode=0)
        result = self.av.validate_restart_container("sonarr")
        self.assertFalse(result.success)
        self.assertIn("NOT healthy", result.reason)

    @patch("action_validator.subprocess.run")
    @patch("action_validator.time.sleep")
    def test_restart_container_not_found(self, mock_sleep, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        result = self.av.validate_restart_container("ghost")
        self.assertFalse(result.success)
        self.assertIn("not found", result.reason)

    @patch("action_validator.urlopen")
    def test_ollama_responding(self, mock_url):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"models": [{"name": "qwen3:8b"}]}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_url.return_value = mock_resp
        result = self.av.validate_restart_ollama()
        self.assertTrue(result.success)

    @patch("action_validator.urlopen", side_effect=Exception("timeout"))
    def test_ollama_not_responding(self, _):
        result = self.av.validate_restart_ollama()
        self.assertFalse(result.success)
        self.assertIn("not responding", result.reason)

    @patch("action_validator.urlopen")
    def test_reload_model_fast(self, mock_url):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"response": "hi"}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_url.return_value = mock_resp
        result = self.av.validate_reload_model("qwen3:8b")
        self.assertTrue(result.success)

    @patch("action_validator.urlopen", side_effect=Exception("model not found"))
    def test_reload_model_failure(self, _):
        result = self.av.validate_reload_model("nonexistent")
        self.assertFalse(result.success)

    @patch("action_validator.subprocess.run")
    def test_cleanup_disk_enough_space(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Filesystem  1M-blocks  Used  Available  Use%  Mounted\n/dev/vda1  50000  30000  20000  60%  /",
            returncode=0)
        result = self.av.validate_cleanup_disk()
        self.assertTrue(result.success)

    @patch("action_validator.subprocess.run")
    def test_cleanup_disk_too_low(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Filesystem  1M-blocks  Used  Available  Use%  Mounted\n/dev/vda1  50000  49800  200  99%  /",
            returncode=0)
        result = self.av.validate_cleanup_disk()
        self.assertFalse(result.success)

    def test_validate_dispatcher_known(self):
        """validate() dispatches to correct validator."""
        mock_fn = MagicMock(return_value=self.av.ValidationResult(
            "restart_container", "sonarr", True, "OK", {}))
        original = self.av._VALIDATORS["validate_restart_container"]
        self.av._VALIDATORS["validate_restart_container"] = mock_fn
        try:
            result = self.av.validate("restart_container", "sonarr",
                                       validator_name="validate_restart_container")
            mock_fn.assert_called_once_with("sonarr")
        finally:
            self.av._VALIDATORS["validate_restart_container"] = original

    def test_validate_dispatcher_none(self):
        """validate() returns None for action without validator."""
        result = self.av.validate("health_check", validator_name=None)
        self.assertIsNone(result)

    def test_validation_result_has_timestamp(self):
        result = self.av.ValidationResult("test", "", True, "ok", {})
        self.assertNotEqual(result.validated_at, "")


# ═══════════════════════════════════════════════════════════════
# Integration: Registry ↔ Safety Guard consistency
# ═══════════════════════════════════════════════════════════════

class TestRegistrySafetyConsistency(unittest.TestCase):
    """Verify action_registry and safety_guard agree on rules."""

    def test_protected_containers_match(self):
        from safety_guard import PROTECTED_CONTAINERS
        from action_registry import get_action
        action = get_action("restart_container")
        for name in PROTECTED_CONTAINERS:
            self.assertIn(name, action["blocked_targets"])

    def test_arr_containers_allowed(self):
        from safety_guard import ARR_CONTAINERS
        from action_registry import can_execute
        for name in ARR_CONTAINERS:
            result = can_execute("restart_container", name)
            self.assertTrue(result["allowed"],
                            f"{name} should be allowed but got: {result}")

    def test_policy_respects_safety_guard(self):
        """Policy engine must block what safety_guard blocks."""
        from policy_engine import evaluate
        for name in ["openclaw", "watchtower"]:
            decision = evaluate("restart_container", name)
            self.assertFalse(decision.allowed,
                             f"{name} should be blocked by policy engine")


if __name__ == "__main__":
    unittest.main(verbosity=2)

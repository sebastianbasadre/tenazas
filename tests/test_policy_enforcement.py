#!/usr/bin/env python3
"""
test_policy_enforcement.py — Security policy tests (MUST ALWAYS PASS).

These are the last line of defense. If any of these fail, the system
has a security regression that must be fixed immediately.
"""

import json
import os
import sys
import unittest
from pathlib import Path

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


# ══════════════════════════════════════════════════════════════
#  Protected containers — NEVER restartable
# ══════════════════════════════════════════════════════════════

class TestProtectedContainersDockerWrapper(unittest.TestCase):
    """docker_wrapper blocks openclaw and watchtower."""

    def test_openclaw_in_blocked(self):
        import docker_wrapper as dw
        self.assertIn("openclaw", dw.BLOCKED_CONTAINERS)

    def test_watchtower_in_blocked(self):
        import docker_wrapper as dw
        self.assertIn("watchtower", dw.BLOCKED_CONTAINERS)

    def test_blocked_containers_case_insensitive(self):
        import docker_wrapper as dw
        blocked_lower = {c.lower() for c in dw.BLOCKED_CONTAINERS}
        self.assertIn("openclaw", blocked_lower)
        self.assertIn("watchtower", blocked_lower)

    def test_exec_not_in_allowed_actions(self):
        import docker_wrapper as dw
        self.assertNotIn("exec", dw.ALLOWED_ACTIONS)

    def test_rm_not_in_allowed_actions(self):
        import docker_wrapper as dw
        self.assertNotIn("rm", dw.ALLOWED_ACTIONS)

    def test_build_not_in_allowed_actions(self):
        import docker_wrapper as dw
        self.assertNotIn("build", dw.ALLOWED_ACTIONS)

    def test_pull_not_in_allowed_actions(self):
        import docker_wrapper as dw
        self.assertNotIn("pull", dw.ALLOWED_ACTIONS)

    def test_only_safe_actions_allowed(self):
        import docker_wrapper as dw
        for action in dw.ALLOWED_ACTIONS:
            self.assertIn(action, {"ps", "restart", "start", "stop"})


class TestProtectedContainersActionRegistry(unittest.TestCase):
    """action_registry blocks openclaw and watchtower."""

    def test_restart_container_blocks_openclaw(self):
        import action_registry as ar
        action = ar.get_action("restart_container")
        self.assertIn("openclaw", action.get("blocked_targets", []))

    def test_restart_container_blocks_watchtower(self):
        import action_registry as ar
        action = ar.get_action("restart_container")
        self.assertIn("watchtower", action.get("blocked_targets", []))

    def test_allowed_targets_no_openclaw(self):
        import action_registry as ar
        action = ar.get_action("restart_container")
        self.assertNotIn("openclaw", action.get("allowed_targets", []))

    def test_allowed_targets_no_watchtower(self):
        import action_registry as ar
        action = ar.get_action("restart_container")
        self.assertNotIn("watchtower", action.get("allowed_targets", []))


class TestProtectedContainersSafetyGuard(unittest.TestCase):
    """safety_guard validates container names."""

    def test_openclaw_not_safe(self):
        import safety_guard as sg
        self.assertFalse(sg.is_safe_container("openclaw"))

    def test_watchtower_not_safe(self):
        import safety_guard as sg
        self.assertFalse(sg.is_safe_container("watchtower"))

    def test_openclaw_uppercase_not_safe(self):
        import safety_guard as sg
        self.assertFalse(sg.is_safe_container("OPENCLAW"))

    def test_watchtower_uppercase_not_safe(self):
        import safety_guard as sg
        self.assertFalse(sg.is_safe_container("WATCHTOWER"))

    def test_injection_attempt_not_safe(self):
        import safety_guard as sg
        self.assertFalse(sg.is_safe_container("sonarr; rm -rf /"))

    def test_pipe_injection_not_safe(self):
        import safety_guard as sg
        self.assertFalse(sg.is_safe_container("sonarr | cat /etc/passwd"))

    def test_backtick_injection_not_safe(self):
        import safety_guard as sg
        self.assertFalse(sg.is_safe_container("sonarr`whoami`"))

    def test_sonarr_is_safe(self):
        import safety_guard as sg
        self.assertTrue(sg.is_safe_container("sonarr"))

    def test_restart_openclaw_blocked(self):
        import safety_guard as sg
        r = sg.validate_action("restart", "openclaw")
        self.assertFalse(r.get("allowed", r.get("safe", True)))

    def test_restart_watchtower_blocked(self):
        import safety_guard as sg
        r = sg.validate_action("restart", "watchtower")
        self.assertFalse(r.get("allowed", r.get("safe", True)))


# ══════════════════════════════════════════════════════════════
#  Action registry — unregistered actions blocked
# ══════════════════════════════════════════════════════════════

class TestActionRegistryEnforcement(unittest.TestCase):

    def test_unknown_action_returns_none(self):
        import action_registry as ar
        self.assertIsNone(ar.get_action("totally_fake_action"))

    def test_registry_has_restart_container(self):
        import action_registry as ar
        self.assertIsNotNone(ar.get_action("restart_container"))

    def test_all_actions_have_risk_level(self):
        import action_registry as ar
        for action in ar.list_actions():
            name = action.get("name", "?")
            self.assertIn("risk_level", action, f"{name} missing risk_level")

    def test_all_actions_have_description(self):
        import action_registry as ar
        for action in ar.list_actions():
            name = action.get("name", "?")
            self.assertIn("description", action, f"{name} missing description")


# ══════════════════════════════════════════════════════════════
#  Staging isolation — MUST be separate from runtime
# ══════════════════════════════════════════════════════════════

class TestStagingIsolation(unittest.TestCase):

    def test_runtime_never_imports_staging(self):
        """No runtime module imports from staging/."""
        for py_file in RUNTIME.glob("*.py"):
            content = py_file.read_text()
            self.assertNotIn("from staging", content,
                             f"{py_file.name} imports from staging/")
            self.assertNotIn("import staging", content,
                             f"{py_file.name} imports staging")

    def test_staging_no_symlinks_to_runtime(self):
        """staging/ should not contain symlinks to runtime/."""
        staging = RUNTIME.parent / "staging"
        if not staging.exists():
            return  # skip if not mounted
        for f in staging.rglob("*"):
            self.assertFalse(f.is_symlink(),
                             f"{f} is a symlink — staging must not link to runtime")

    def test_staging_has_no_py_in_root(self):
        """staging/ root should not have .py files that could be imported."""
        staging = RUNTIME.parent / "staging"
        if not staging.exists():
            return
        root_py = [f for f in staging.glob("*.py")]
        self.assertEqual(root_py, [],
                         f"staging/ root has .py files: {root_py}")

    def test_staging_and_runtime_different_dirs(self):
        staging = RUNTIME.parent / "staging"
        self.assertNotEqual(RUNTIME.resolve(), staging.resolve() if staging.exists() else Path("/none"))


# ══════════════════════════════════════════════════════════════
#  Approval requirements — HIGH/MEDIUM need approval
# ══════════════════════════════════════════════════════════════

class TestApprovalRequirements(unittest.TestCase):

    def test_action_registry_has_approval_field(self):
        import action_registry as ar
        for action in ar.list_actions():
            name = action.get("name", "?")
            self.assertIn("requires_approval", action, f"{name} missing requires_approval")

    def test_all_actions_have_cooldown(self):
        import action_registry as ar
        for action in ar.list_actions():
            name = action.get("name", "?")
            self.assertIn("cooldown_seconds", action, f"{name} missing cooldown")
            self.assertGreater(action["cooldown_seconds"], 0)


if __name__ == "__main__":
    unittest.main()

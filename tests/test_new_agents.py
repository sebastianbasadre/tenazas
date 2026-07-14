#!/usr/bin/env python3

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1] / "workspace" / "runtime"
if str(RUNTIME.parent) not in sys.path:
    sys.path.insert(0, str(RUNTIME.parent))
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from agent_protocol import Observation


NEW_AGENTS = {
    "agent_portfolio_risk": (
        "portfolio risk allocation",
        {"total_usd": 1000, "top": [{"symbol": "BTC", "value_usd": 500}], "issues": []},
    ),
    "agent_subscriptions": (
        "subscription bills",
        {"recurring": [{"name": "spotify", "count": 2, "recent_total": 1000}]},
    ),
    "agent_job_search": (
        "job interview",
        {"active": 1, "interviews": 1, "offers": 0, "items": []},
    ),
    "agent_home_reliability": (
        "home devices wifi",
        {"devices": 3, "offline": []},
    ),
    "agent_media_stack": (
        "media stack downloads",
        {"containers": 3, "up": 3, "down": []},
    ),
    "agent_daily_brief": (
        "daily brief today",
        {"sections": ["Calendar: clear", "Infra: ok"]},
    ),
    "agent_runbook": (
        "incident runbook",
        {"event_types": ["container_down"], "recent_events": [{"type": "container_down"}]},
    ),
}


class NewAgentContractTests(unittest.TestCase):
    def test_new_agents_own_expected_queries_and_return_observation(self):
        for module_name, (query, status) in NEW_AGENTS.items():
            with self.subTest(module=module_name):
                mod = importlib.import_module(module_name)
                self.assertTrue(mod.owns(query))
                with patch.object(mod, "_log", lambda *_a, **_kw: None), \
                     patch.object(mod, "assess", return_value=status):
                    obs, recs = mod.handle(query, correlation_id="test123")
                self.assertIsInstance(obs, Observation)
                self.assertIsInstance(recs, list)
                self.assertTrue(obs.summary)

    def test_coordinator_registers_new_agents(self):
        import agent_coordinator

        for name in (
            "portfolio_risk", "subscriptions", "job_search", "home_reliability",
            "media_stack", "daily_brief", "runbook",
        ):
            self.assertIn(name, agent_coordinator.AGENT_MODULES)

    def test_registry_defaults_include_new_agents(self):
        import agent_registry

        for name in (
            "security", "portfolio_risk", "subscriptions", "job_search",
            "home_reliability", "media_stack", "daily_brief", "runbook",
        ):
            self.assertIn(name, agent_registry.DEFAULT_AGENTS)


if __name__ == "__main__":
    unittest.main()

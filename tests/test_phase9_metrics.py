#!/usr/bin/env python3
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


class TestPhase9Metrics(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_agent_metrics_records_agent_totals_and_latency(self):
        import agent_metrics as am
        metrics_file = self.tmpdir / "agent_metrics_state.json"
        with patch.object(am, "METRICS_STATE_FILE", metrics_file):
            am.record_agent_result("infra", 1.5, "ok")
            am.record_agent_result("infra", 0.5, "timeout")
        data = json.loads(metrics_file.read_text())
        infra = data["per_agent"]["infra"]
        self.assertEqual(infra["queries_total"], 2)
        self.assertEqual(infra["outcomes"]["ok"], 1)
        self.assertEqual(infra["outcomes"]["timeout"], 1)
        self.assertAlmostEqual(infra["latency_avg"], 1.0)

    def test_agent_metrics_records_routes_and_fallbacks(self):
        import agent_metrics as am
        metrics_file = self.tmpdir / "agent_metrics_state.json"
        with patch.object(am, "METRICS_STATE_FILE", metrics_file):
            am.record_route_result("parallel", True)
            am.record_route_result("skill_fallback", False, "llm_needed")
        data = json.loads(metrics_file.read_text())
        self.assertEqual(data["coordinator"]["routes"]["parallel"], 1)
        self.assertEqual(data["coordinator"]["routes"]["skill_fallback"], 1)
        self.assertEqual(data["coordinator"]["fallbacks"]["llm_needed"], 1)
        self.assertEqual(data["coordinator"]["handled"]["true"], 1)
        self.assertEqual(data["coordinator"]["handled"]["false"], 1)

    def test_coordinator_single_success_records_metrics(self):
        import agent_coordinator as ac
        with patch.object(ac, "_load_agent") as mock_load:
            mock_agent = type("A", (), {"handle": staticmethod(lambda _q, correlation_id=None: (type("O", (), {"summary": "ok"})(), []))})
            mock_load.return_value = mock_agent
            with patch.object(ac, "record_agent_result") as mock_record:
                result = ac._run_single("infra", "query", "abc12345")
        self.assertIn("infra", result)
        mock_record.assert_called_once()
        self.assertEqual(mock_record.call_args[0][0], "infra")

    def test_metrics_server_reads_agent_metrics_state(self):
        import metrics_server as ms
        if not getattr(ms, "PROM_AVAILABLE", False):
            self.skipTest("prometheus_client not available in local test environment")
        state = {
            "per_agent": {
                "infra": {
                    "queries_total": 2,
                    "latency_last": 1.2,
                    "latency_avg": 0.8,
                    "outcomes": {"ok": 1, "error": 1, "timeout": 0},
                }
            },
            "coordinator": {
                "routes": {"parallel": 3},
                "fallbacks": {"llm_needed": 1},
                "handled": {"true": 2, "false": 1},
            },
        }
        path = self.tmpdir / "agent_metrics_state.json"
        path.write_text(json.dumps(state))
        with patch.object(ms, "AGENT_METRICS_STATE_FILE", path):
            ms.update_agent_metrics()
        self.assertEqual(ms.G_AGENT_QUERIES.labels(agent="infra")._value.get(), 2)
        self.assertEqual(ms.G_COORD_ROUTES.labels(strategy="parallel")._value.get(), 3)
        self.assertEqual(ms.G_COORD_FALLBACKS.labels(source="llm_needed")._value.get(), 1)

    def test_dashboard_contains_phase9_metric_names(self):
        dashboard = Path("grafana-tenazas-agent.json").read_text(encoding="utf-8")
        self.assertIn("tenazas_agent_queries_total", dashboard)
        self.assertIn("tenazas_coordinator_routes_total", dashboard)

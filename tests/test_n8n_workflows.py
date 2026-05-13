import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "workspace" / "runtime"
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

import job_runner


WORKFLOW_EXPECTATIONS = {
    "health-sweep.json": {"job": "health_sweep", "timeout_ms": 240000},
    "gpu-keepalive.json": {"job": "gpu_keepalive", "timeout_ms": 90000},
    "autofix-agent.json": {"job": "autofix_agent", "timeout_ms": 1200000},
    "proposal-executor.json": {"job": "proposal_executor", "timeout_ms": 300000},
    "portfolio-reconciler.json": {"job": "portfolio_reconciler", "timeout_ms": 180000},
    "iol-refresh.json": {"job": "iol_refresh", "timeout_ms": 120000},
    "notification-calendar.json": {"job": "notification_calendar", "timeout_ms": 90000},
    "notification-email.json": {"job": "notification_email", "timeout_ms": 180000},
    "finance-daily.json": {"job": "finance_daily", "timeout_ms": 180000},
}


class TestN8nWorkflows(unittest.TestCase):
    def test_workflow_set_matches_job_runner_allowlist(self):
        workflow_dir = ROOT / "n8n" / "workflows"
        actual = {path.name for path in workflow_dir.glob("*.json")}
        self.assertEqual(actual, set(WORKFLOW_EXPECTATIONS))
        self.assertEqual(set(job_runner.JOBS), {entry["job"] for entry in WORKFLOW_EXPECTATIONS.values()})

    def test_workflows_target_expected_job_runner_endpoints(self):
        workflow_dir = ROOT / "n8n" / "workflows"
        for filename, expected in WORKFLOW_EXPECTATIONS.items():
            with self.subTest(workflow=filename):
                payload = json.loads((workflow_dir / filename).read_text(encoding="utf-8"))
                http_nodes = [
                    node for node in payload.get("nodes", [])
                    if node.get("type") == "n8n-nodes-base.httpRequest"
                ]
                self.assertEqual(len(http_nodes), 1)
                node = http_nodes[0]
                self.assertIn(f"/run/{expected['job']}", node["parameters"]["url"])
                self.assertIn("http://host.docker.internal:18891", node["parameters"]["url"])
                self.assertTrue(node["parameters"].get("sendHeaders"))

                headers = node["parameters"]["headerParameters"]["parameters"]
                token_header = next((h for h in headers if h.get("name") == "X-Tenazas-Job-Token"), None)
                self.assertIsNotNone(token_header)
                self.assertIn("TENAZAS_JOB_RUNNER_TOKEN", token_header["value"])

                timeout = node["parameters"].get("options", {}).get("timeout")
                self.assertEqual(timeout, expected["timeout_ms"])


if __name__ == "__main__":
    unittest.main()

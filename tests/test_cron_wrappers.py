import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"

EXPECTED_WRAPPERS = {
    "autofix_agent_cron.sh": {"job": "autofix_agent", "timeout": "1200"},
    "finance_daily_cron.sh": {"job": "finance_daily", "timeout": "180"},
    "gpu_keepalive_cron.sh": {"job": "gpu_keepalive", "timeout": "90"},
    "health_sweep_cron.sh": {"job": "health_sweep", "timeout": "240"},
    "iol_refresh_cron.sh": {"job": "iol_refresh", "timeout": "120"},
    "notification_calendar_cron.sh": {"job": "notification_calendar", "timeout": "90"},
    "notification_email_cron.sh": {"job": "notification_email", "timeout": "180"},
    "portfolio_reconciler_cron.sh": {"job": "portfolio_reconciler", "timeout": "180"},
    "proposal_executor_cron.sh": {"job": "proposal_executor", "timeout": "300"},
}


class TestCronWrappers(unittest.TestCase):
    def test_helper_supports_runner_mode(self):
        content = (SCRIPTS_DIR / "run_openclaw_job.sh").read_text(encoding="utf-8")
        self.assertIn('OPENCLAW_JOB_MODE', content)
        self.assertIn('OPENCLAW_JOB_RUNNER_URL', content)
        self.assertIn('OPENCLAW_JOB_RUNNER_TOKEN', content)
        self.assertIn('/run/${JOB_NAME}', content)

    def test_wrappers_point_to_expected_jobs_and_timeouts(self):
        for filename, expected in EXPECTED_WRAPPERS.items():
            with self.subTest(script=filename):
                content = (SCRIPTS_DIR / filename).read_text(encoding="utf-8")
                self.assertIn('run_openclaw_job.sh', content)
                self.assertIn(f"\n  {expected['timeout']} \\\n", content)
                self.assertIn(f"\n  {expected['job']} \\\n", content)

    def test_gpu_wrapper_uses_runtime_path(self):
        content = (SCRIPTS_DIR / "gpu_keepalive_cron.sh").read_text(encoding="utf-8")
        self.assertIn("/home/tenazas/runtime/gpu_scheduler.py --keepalive", content)
        self.assertNotIn("/home/tenazas/workspace/runtime/gpu_scheduler.py", content)


if __name__ == "__main__":
    unittest.main()

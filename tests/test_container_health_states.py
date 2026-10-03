from unittest.mock import MagicMock, patch

import homelab_goals as goals
import homelab_snapshot as snapshot
import telegram_router as router


def test_stopped_containers_do_not_fail_healthcheck_goal():
    result = MagicMock(returncode=0, stdout=(
        "openclaw\tUp 2 hours (healthy)\n"
        "WATCHTOWER\tExited (0) 9 days ago\npiper\tExited (0) 9 days ago\n"
    ))
    with patch.object(goals.subprocess, "run", return_value=result):
        status, evidence = goals._eval_containers_healthy()
    assert status == "ok"
    assert "0 unhealthy" in evidence
    assert "stopped: WATCHTOWER, piper" in evidence


def test_unhealthy_name_does_not_count_as_failed_healthcheck():
    result = MagicMock(returncode=0, stdout="unhealthy-test\tUp 2 hours\n")
    with patch.object(snapshot.subprocess, "run", return_value=result):
        assert snapshot._collect_containers()["unhealthy"] == 0


def test_docker_failure_cannot_report_zero_unhealthy():
    with patch.object(goals.subprocess, "run", return_value=MagicMock(returncode=1)):
        assert goals._eval_containers_healthy()[0] == "unknown"


def test_status_reports_stopped_and_unhealthy_separately():
    with patch.object(snapshot, "_collect_containers", return_value={
        "total": 28, "running": 26, "stopped": 2, "unhealthy": 0,
    }):
        response = router._slash_command_response("/status")
    assert "26 activos, 2 detenidos, 0 con healthcheck unhealthy" in response

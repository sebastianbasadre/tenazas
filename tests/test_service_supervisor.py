from unittest.mock import MagicMock, patch

import service_supervisor as ss
from recovery_watchdog import eligible


def test_failed_service_is_scheduled_for_restart():
    service = ss.Service("test", ["false"], process=MagicMock(), started=10)
    service.process.poll.return_value = 1
    service.process.returncode = 1
    service.logger = MagicMock()
    with patch.object(ss, "stop", side_effect=lambda item: setattr(item, "process", None)):
        ss.check(service, 11)
    assert service.restarts == 1
    assert service.next_start == 16


def test_periodic_success_waits_until_next_slot():
    service = ss.Service("runtime_cycle", ["true"], interval=300, process=MagicMock(), started=10)
    service.process.poll.return_value = 0
    service.process.returncode = 0
    service.logger = MagicMock()
    with patch.object(ss, "stop", side_effect=lambda item: setattr(item, "process", None)):
        ss.check(service, 20)
    assert service.next_start == 310
    assert service.restarts == 0


def test_healthy_service_resets_backoff():
    service = ss.Service(
        "metrics",
        ["true"],
        probe="http://localhost/health",
        process=MagicMock(),
        started=1,
        last_probe=1,
        failures=4,
    )
    service.process.poll.return_value = None
    with patch.object(ss, "urlopen") as response:
        response.return_value.__enter__.return_value.status = 200
        ss.check(service, 350)
    assert service.failures == 0


def test_container_recovery_has_cooldown_and_hourly_budget():
    container = {"State": {"Running": True, "Health": {"Status": "unhealthy"}}}
    assert eligible(container, [], 1000)
    assert not eligible(container, [999], 1000)
    assert not eligible(container, [400, 600, 800], 1000)
    container["State"]["Health"]["Status"] = "healthy"
    assert not eligible(container, [], 1000)

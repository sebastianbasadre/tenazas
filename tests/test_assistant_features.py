import json
import os
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import assistant_commands as commands
import assistant_features as features
import assistant_jobs as jobs
import assistant_schedule as schedule
import assistant_worker as worker
import document_library as documents
import user_memory
import verified_routines as routines


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "DB", tmp_path / "jobs.sqlite")
    monkeypatch.setattr(commands, "ROOT", tmp_path)
    monkeypatch.setattr(features, "ROOT", tmp_path)
    monkeypatch.setattr(documents, "LIBRARY", tmp_path / "documents")
    monkeypatch.setattr(documents, "INDEX", tmp_path / "documents.sqlite")
    monkeypatch.setattr(schedule, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(user_memory, "PROFILE_PATH", tmp_path / "profile.json")
    monkeypatch.setattr(user_memory, "CONTEXT_PATH", tmp_path / "context.json")
    monkeypatch.setattr(user_memory, "MEMORY_EVENTS_LOG", tmp_path / "memory.jsonl")
    worker.STOP.clear()


def test_job_ownership_controls_cancel_approval_and_lookup():
    store = jobs.Jobs()
    item = store.create("123", "cv", {"opportunity_id": "vacancy-1"})
    store.update(item["id"], status="waiting_approval", result={"application_id": "app-1"})
    assert store.get(item["id"], "999") is None
    assert not store.cancel(item["id"], "999")
    assert not store.approve(item["id"], "999")
    assert store.approve(item["id"], "123")
    assert not store.approve(item["id"], "123")
    assert store.get(item["id"])["kind"] == "apply"


def test_scheduled_job_is_deduplicated_and_claimed_once():
    store = jobs.Jobs()
    first = store.create("123", "daily", schedule_key="daily-1")
    assert store.create("123", "daily", schedule_key="daily-1")["id"] == first["id"]
    assert store.claim()["id"] == first["id"]
    assert store.claim() is None


def test_cancellation_wins_a_completion_race():
    store = jobs.Jobs()
    item = store.create("123", "daily")
    store.claim()
    assert store.cancel(item["id"], "123")
    store.finish(item["id"], "done", {"text": "result"})
    assert store.get(item["id"])["status"] == "cancelled"


def test_crashed_side_effect_job_is_not_replayed():
    store = jobs.Jobs()
    item = store.create("123", "apply")
    store.claim()
    store.recover()
    assert store.get(item["id"])["status"] == "interrupted"
    assert store.claim() is None


def test_memory_correction_and_forgetting_affect_injected_context():
    assert "Actualicé" in commands.memory('poner projects ["Proyecto Faro"]')
    assert "Proyecto Faro" in user_memory.build_memory_context("mi proyecto")
    assert commands.memory("borrar projects") == "Preferencia borrada."
    assert "Proyecto Faro" not in user_memory.build_memory_context("mi proyecto")
    commands.memory("limpiar")
    assert user_memory.get_profile() == {}


def test_memory_rejects_secrets_and_wrong_types():
    assert "no editable" in commands.memory("poner api_key secret")
    assert "lista JSON" in commands.memory("poner projects texto")


def test_document_sources_are_owner_scoped_and_deletable(tmp_path):
    folder = documents.LIBRARY / "123"
    folder.mkdir(parents=True)
    path = folder / "manual.txt"
    path.write_text("Para reiniciar el router: mantener RESET durante diez segundos.")
    item = documents.ingest(path, "123", "manual.txt")
    response = documents.query("reiniciar router", "123")
    assert "manual.txt" in response and "diez segundos" in response
    assert "No encontré" in documents.query("reiniciar router", "999")
    assert not documents.delete(item["document"], "999")
    assert documents.delete(item["document"], "123")
    assert not path.exists()
    assert "No encontré" in documents.query("reiniciar router", "123")


def test_document_ingest_cannot_read_files_outside_library(tmp_path):
    private = tmp_path / "private.txt"
    private.write_text("secret")
    with pytest.raises(ValueError):
        documents.ingest(private, "123")


def test_scheduler_honors_time_and_deduplicates(monkeypatch):
    monkeypatch.setattr(schedule, "get_default_telegram_chat_id", lambda: "123")
    created = datetime(2026, 10, 5, 7, tzinfo=schedule.TZ)
    schedule.write_atomic(schedule.SETTINGS, {"created_at": created.timestamp(), "daily": "09:00", "weekly": "18:00", "weekday": 4,
                                           "daily_enabled": True, "weekly_enabled": False})
    schedule.tick(created.replace(hour=8))
    assert jobs.Jobs().list("123") == []
    schedule.tick(created.replace(hour=9))
    schedule.tick(created.replace(hour=10))
    assert len(jobs.Jobs().list("123")) == 1


def test_paused_schedule_does_not_send_or_enqueue(monkeypatch):
    monkeypatch.setattr(schedule, "get_default_telegram_chat_id", lambda: "123")
    schedule.configure("daily", "off")
    assert schedule.enqueue_daily() is None


def test_cv_job_freezes_selected_vacancy_id():
    with patch("agent_job_search._ranked_opportunities", return_value=[{"id": "fixed-id"}]):
        commands.handle("/cv 1", "123")
    assert jobs.Jobs().list("123")[0]["payload"]["opportunity_id"] == "fixed-id"


def test_approval_is_required_before_external_submission():
    with patch("job_application_manager.submit") as submit:
        with pytest.raises(ValueError):
            features.submit_application({"prepared": {"application_id": "app-1"}})
    submit.assert_not_called()


def test_failed_device_readback_rolls_back_instead_of_claiming_success():
    prepared = {"name": "cine", "before": {"tv_online": False}}
    with patch.object(routines, "plan", return_value=prepared), \
         patch.object(routines.tv, "_detect_target", return_value={"ip": "127.0.0.1"}), \
         patch.object(routines.tv, "execute", return_value={"success": True}), \
         patch.object(routines.tv, "_is_online", return_value=True), \
         patch.object(routines.avr, "execute", return_value={"success": True}), \
         patch.object(routines.avr, "status", return_value={"success": True, "output": "Power: ON\nInput: TV"}), \
         patch.object(routines.hue, "execute"), patch.object(routines, "check_lights", return_value=False), \
         patch.object(routines, "rollback", return_value=["restaurado"]) as rollback:
        result = routines.execute(prepared, {}, lambda: False, lambda _: None)
    assert not result["ok"]
    assert "restaurado" in result["text"]
    rollback.assert_called_once()


def test_changed_device_state_requires_new_approval():
    with patch.object(routines, "plan", return_value={"before": {"tv_online": True}}), patch.object(routines.tv, "execute") as execute:
        result = routines.execute({"name": "cine", "before": {"tv_online": False}}, {}, lambda: False, lambda _: None)
    assert not result["ok"]
    execute.assert_not_called()


def test_weekly_report_uses_failures_to_propose_a_concrete_improvement():
    store = jobs.Jobs()
    item = store.create("123", "search")
    store.finish(item["id"], "failed", {"text": "source failed"})
    report = features.weekly("123")
    assert "search: 1" in report
    assert "Revisar primero search" in report


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="Linux process groups")
def test_running_job_cancellation_terminates_the_child(tmp_path, monkeypatch):
    script = tmp_path / "assistant_tasks.py"
    script.write_text("import time\ntime.sleep(60)\n")
    monkeypatch.setattr(worker, "BASE", tmp_path)
    store = jobs.Jobs()
    item = store.create("123", "daily")
    claimed = store.claim()
    timer = threading.Timer(.5, lambda: store.cancel(item["id"], "123"))
    timer.start()
    started = time.monotonic()
    worker.run_job(claimed, store)
    timer.join()
    assert time.monotonic() - started < 5
    assert store.get(item["id"])["status"] == "cancelled"


def test_optional_stopped_services_do_not_fail_health_sweep():
    import health_sweep
    with patch.object(health_sweep, "_load_config", return_value={}), \
         patch.object(health_sweep, "_run_command", return_value=(MagicMock(stdout="false"), .1)):
        assert health_sweep._probe_piper()["status"] == "ok"
    with patch.object(health_sweep, "_load_config", return_value={"expected_containers": ["piper"]}), \
         patch.object(health_sweep, "_run_command", return_value=(MagicMock(stdout="false"), .1)):
        assert health_sweep._probe_piper()["status"] == "fail"


def test_legacy_briefing_queues_instead_of_sending_a_duplicate():
    import unified_briefing
    with patch.object(schedule, "enqueue_daily", return_value={"id": "existing"}), patch.object(unified_briefing, "_notify") as notify:
        result = unified_briefing.send_briefing()
    assert result["queued"] and result["job_id"] == "existing"
    notify.assert_not_called()


def test_document_index_job_produces_searchable_citations():
    import assistant_tasks
    folder = documents.LIBRARY / "123"
    folder.mkdir(parents=True)
    path = folder / "manual.txt"
    path.write_text("La bomba necesita mantenimiento trimestral.", encoding="utf-8")
    result = assistant_tasks.perform({"kind": "document_index", "chat": "123",
                                      "payload": {"path": str(path), "filename": "manual.txt"}})
    assert "Documento indexado" in result["text"]
    assert "manual.txt" in documents.query("mantenimiento bomba", "123")

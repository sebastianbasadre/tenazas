"""ChatGPT job assistant: contracts, isolated data and fixed SSH transport."""

import difflib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tools.deploy_applications_mcp import apply_text
from workspace.runtime import mcp_applications_contract as contract
from workspace.runtime.mcp_applications_client import ApplicationsClient

import application_mail
import application_mcp_runner as runner
import assistant_jobs
from application_tracker import Applications


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant_jobs, "DB", tmp_path / "applications.sqlite")


def test_chatgpt_can_track_without_telegram_or_external_send(monkeypatch):
    created = runner.dispatch(
        "register_job_application",
        {"company": "Acme", "role": "Product Manager", "cv": "CV-v2.pdf"},
    )
    identity = created["id"]
    assert "chat" not in created and "fingerprint" not in created
    assert runner.dispatch("get_job_applications", {})["total"] == 1
    runner.dispatch(
        "update_job_application", {"id": identity, "kind": "status", "value": "interview"}
    )
    assert runner.dispatch("get_job_application", {"id": identity})["status"] == "interview"
    draft = runner.dispatch("draft_job_followup", {"id": identity})
    assert draft["sent"] is False and "Acme" in draft["draft"]
    runner.dispatch("delete_job_application", {"id": identity, "confirm": True})
    assert runner.dispatch("get_job_applications", {})["total"] == 0


def test_owner_is_not_supplied_by_chatgpt_and_legacy_other_owner_is_hidden():
    Applications().add("123", "Private Employer", "Private role")
    assert runner.dispatch("get_job_applications", {})["applications"] == []
    with pytest.raises(ValueError):
        runner.dispatch("get_job_applications", {"owner": "123"})


@pytest.mark.parametrize(
    "name,args",
    [
        ("exec", {"command": "whoami"}),
        ("register_job_application", {"company": "A", "role": "B", "email_password": "PRIVATE"}),
        ("update_job_application", {"id": "a" * 10, "kind": "send_email"}),
        ("get_job_application", {"id": "../private"}),
        ("delete_job_application", {"id": "a" * 10, "confirm": False}),
        ("delete_job_application", {"id": "a" * 10, "confirm": 1}),
        ("get_job_applications", {"offset": True}),
    ],
)
def test_no_commands_paths_credentials_or_unconfirmed_delete(name, args):
    with pytest.raises(ValueError):
        contract.validate(name, args)


def test_mail_refresh_is_durable_deduplicated_and_does_not_notify_telegram(monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    first = runner.dispatch("refresh_job_mail", {})
    assert runner.dispatch("refresh_job_mail", {}) == first
    assert spawn.call_count == 1
    with assistant_jobs.Jobs().connect() as c:
        assert c.execute("SELECT count(*) FROM assistant_jobs").fetchone()[0] == 0
    monkeypatch.setattr(application_mail, "scan", lambda owner: {"new": 0, "truncated": False})
    runner.mail_worker(first["id"])
    result = runner.dispatch("get_job_mail_review", {"job_id": first["id"]})
    assert result["scan"]["status"] == "done" and result["suggestions"] == []


def test_gmail_failure_stays_failure_and_hides_details(monkeypatch):
    monkeypatch.setattr(runner.subprocess, "Popen", Mock())
    job = runner.queue_mail()
    monkeypatch.setattr(
        application_mail, "scan", Mock(side_effect=RuntimeError("PRIVATE_PASSWORD"))
    )
    runner.mail_worker(job["id"])
    result = runner.dispatch("get_job_mail_review", {"job_id": job["id"]})
    assert result["scan"]["status"] == "failed"
    assert "PRIVATE" not in json.dumps(result)


def test_annotations_and_write_permission_are_distinct():
    definitions = contract.tools()
    assert len(definitions) == 9
    for tool in definitions:
        writable = tool["name"] in contract.WRITE_TOOLS
        assert tool["annotations"]["readOnlyHint"] is not writable
        assert (contract.WRITE_SCOPE in tool["securitySchemes"][0]["scopes"]) is writable


def test_ssh_destination_verification_and_arguments_are_fixed(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("SYNTHETIC_KEY_FIXTURE")
    key.chmod(0o600)
    known = tmp_path / "known_hosts"
    known.write_text("SYNTHETIC_HOST_FIXTURE")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"key_file": str(key), "known_hosts": str(known)}))
    captured = []

    def run(args, **kwargs):
        captured.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'{"ok":true,"data":{"total":0}}')

    monkeypatch.setattr("workspace.runtime.mcp_applications_client.subprocess.run", run)
    assert ApplicationsClient(config).call("get_job_applications", {})["ok"]
    args, kwargs = captured[0]
    assert args[-1] == "sbasadre@192.168.2.2"
    assert "StrictHostKeyChecking=yes" in args and "IdentitiesOnly=yes" in args
    assert json.loads(kwargs["input"])["name"] == "get_job_applications"
    assert "shell" not in kwargs


def test_installer_patch_preserves_unrelated_installed_code():
    before = 'PRESERVED = "installed"\ndef handler():\n    return 1\n'
    proposed = before.replace("return 1", "return 2")
    patch = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True), proposed.splitlines(keepends=True), n=0
        )
    )
    assert apply_text(before, patch) == proposed
    with pytest.raises(ValueError, match="review required"):
        apply_text(before.replace("return 1", "return 3"), patch)

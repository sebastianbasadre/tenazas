import importlib
import sys
from pathlib import Path


RUNTIME = Path(__file__).resolve().parents[1] / "workspace" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

manager = importlib.import_module("job_application_manager")


def test_linkedin_approval_never_reports_false_submission(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "STATE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(manager, "LINKEDIN_BRIDGE_TOKEN_FILE", tmp_path / "missing-token")
    entry = manager.stage(
        {
            "id": "job-1",
            "title": "Product Manager",
            "company": "Example",
            "url": "https://linkedin.com/jobs/view/1",
            "match_score": 80,
        },
        {"path": "/tmp/cv.pdf", "filename": "cv.pdf", "strengths": [], "gaps": []},
    )
    result = manager.submit(entry["id"])
    assert result["ok"] is False
    assert result["status"] == "bridge_unavailable"


def test_stage_deduplicates_pending_application(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "STATE_FILE", tmp_path / "queue.json")
    opportunity = {
        "id": "job-1",
        "title": "Product Manager",
        "company": "Example",
        "url": "https://example.com/job",
        "match_score": 80,
    }
    cv = {"path": "/tmp/cv.pdf", "filename": "cv.pdf"}
    assert manager.stage(opportunity, cv)["id"] == manager.stage(opportunity, cv)["id"]

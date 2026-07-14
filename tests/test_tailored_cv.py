import importlib
import sys
from pathlib import Path
from pypdf import PdfReader


RUNTIME = Path(__file__).resolve().parents[1] / "workspace" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

jobs = importlib.import_module("agent_job_search")


def test_prepare_tailored_cv_revalidates_and_generates(tmp_path, monkeypatch):
    master = tmp_path / "master.txt"
    master.write_text(
        "CANDIDATE\nProduct Manager\nmail@example.com\nPROFESSIONAL SUMMARY\n"
        "Product Manager with AWS cloud roadmap agile stakeholder experience.\n"
        "CORE COMPETENCIES\nAWS · Cloud · Agile · Roadmap\n"
        "PROFESSIONAL EXPERIENCE\nProduct Manager\nBuilt cloud products.\n"
        "CERTIFICATIONS\nScrum\nEDUCATION\nDegree\nTECHNICAL SKILLS\nAWS\nLANGUAGES\nEnglish",
        encoding="utf-8",
    )
    monkeypatch.setattr(jobs, "MASTER_CV_FILE", master)
    monkeypatch.setattr(jobs, "TAILORED_CV_DIR", tmp_path / "tailored")
    monkeypatch.setattr(
        jobs,
        "_ranked_opportunities",
        lambda: [{"id": "1", "title": "Product Manager Cloud", "company": "Example", "url": "x"}],
    )
    monkeypatch.setattr(
        jobs,
        "_inspect_job_page",
        lambda _url: {
            "accepting": True,
            "posted_date": "2026-06-25",
            "description": "Product Manager AWS cloud roadmap agile stakeholder",
        },
    )
    monkeypatch.setattr(jobs, "_is_recent", lambda _date: True)
    result = jobs.prepare_tailored_cv(1)
    assert result["score"] >= 65
    assert Path(result["path"]).exists()
    assert result["filename"].endswith(".pdf")
    assert len(PdfReader(result["path"]).pages) <= 2

import importlib
import sys
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "workspace" / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

jobs = importlib.import_module("agent_job_search")


@pytest.fixture
def isolated_jobs(tmp_path, monkeypatch):
    opportunities = tmp_path / "opportunities.json"
    status = tmp_path / "status.json"
    profile = tmp_path / "profile.json"
    profile.write_text(
        """{
          "city": "Buenos Aires",
          "country": "Argentina",
          "roles_target": ["Product Manager", "DevOps"],
          "tools": ["Docker", "Ollama", "AWS"]
        }""",
        encoding="utf-8",
    )
    monkeypatch.setattr(jobs, "OPPS_FILE", opportunities)
    monkeypatch.setattr(jobs, "STATUS_FILE", status)
    monkeypatch.setattr(jobs, "PROFILE_FILE", profile)
    monkeypatch.setattr(jobs, "_log", lambda *_args, **_kwargs: None)
    return opportunities


def test_scores_role_skills_and_remote(isolated_jobs):
    match = jobs.score_opportunity(
        {
            "title": "Technical Product Manager",
            "description": "Remote role working with Docker and AWS",
        }
    )
    assert match["score"] >= 70
    assert any(reason.startswith("rol:") for reason in match["reasons"])
    assert any(reason.startswith("skills:") for reason in match["reasons"])


def test_tam_alias_scores_role_but_company_name_does_not(isolated_jobs):
    role = jobs.score_opportunity(
        {"title": "Senior Technical Account Manager", "description": "Remote"}
    )
    company = jobs.score_opportunity(
        {
            "title": "TAM Acceleration busca personal para el cargo de Sales Representative",
            "description": "Argentina",
        }
    )
    assert role["score"] >= 50
    assert company["score"] < 50


def test_upsert_deduplicates_url_and_preserves_pipeline_status(isolated_jobs):
    first = jobs.add_opportunities(
        [{"title": "DevOps Engineer", "url": "https://example.com/jobs/1?ref=feed"}]
    )
    assert first["added"] == 1
    opportunity_id = jobs._load_opportunities()[0]["id"]
    jobs.update_status(opportunity_id, "applied", "CV enviado")

    second = jobs.add_opportunities(
        [{"title": "Senior DevOps Engineer", "url": "https://example.com/jobs/1"}]
    )
    stored = jobs._load_opportunities()
    assert second == {"added": 0, "updated": 1, "total": 1}
    assert stored[0]["status"] == "applied"
    assert stored[0]["notes"][0]["text"] == "CV enviado"


def test_discover_ingests_search_results(isolated_jobs):
    def fake_search(_query, num_results, language):
        assert num_results >= 3
        assert language == "es"
        return [
            {
                "title": "Product Manager remoto",
                "url": "https://jobs.example.com/pm",
                "snippet": "Producto, Docker, Buenos Aires",
            }
        ]

    result = jobs.discover("buscar trabajo de producto", search_fn=fake_search)
    assert result["added"] == 1
    assert result["rejected"] == 0
    assert result["errors"] == []
    assert jobs.assess()["active"] == 1


def test_search_queries_target_individual_job_pages(isolated_jobs):
    queries = jobs._search_queries(jobs._load_profile())
    assert any(
        '"Product Manager"' in query and "site:linkedin.com/jobs/view" in query
        for query in queries
    )
    assert any("site:boards.greenhouse.io" in query for query in queries)
    assert any("site:jobs.lever.co" in query for query in queries)
    assert any('"technical account manager"' in query for query in queries)


def test_discover_rejects_spam_aggregates_and_irrelevant_results(isolated_jobs):
    def fake_search(_query, num_results, language):
        return [
            {
                "title": "+1000 empleos de «谷歌蜘蛛池出租» - LinkedIn",
                "url": "https://ar.linkedin.com/jobs/spam-jobs",
                "snippet": "TAM remoto Argentina",
            },
            {
                "title": "35 empleos de «Devops» en Argentina - LinkedIn",
                "url": "https://ar.linkedin.com/jobs/devops-empleos",
                "snippet": "Los empleos más populares",
            },
            {
                "title": "PRODUCT | Cambridge Dictionary",
                "url": "https://dictionary.example/product",
                "snippet": "Definition of product",
            },
            {
                "title": "DevOps Engineer - Example",
                "url": "https://ar.linkedin.com/jobs/view/devops-engineer-123",
                "snippet": "Remote, Docker and AWS",
            },
        ]

    result = jobs.discover("buscar trabajo DevOps remoto", search_fn=fake_search)
    assert result["added"] == 1
    assert result["rejected"] >= 3
    assert jobs._load_opportunities()[0]["title"] == "DevOps Engineer - Example"


def test_prune_only_removes_untouched_invalid_discoveries(isolated_jobs):
    jobs.add_opportunities(
        [
            {
                "title": "Empleos de Product Manager en Argentina - LinkedIn",
                "url": "https://ar.linkedin.com/jobs/product-manager-empleos",
                "source": "searxng",
            },
            {
                "title": "DevOps Engineer",
                "url": "https://example.com/jobs/devops-1",
                "source": "manual",
            },
        ]
    )
    result = jobs.prune_invalid_discoveries()
    stored = jobs._load_opportunities()
    assert result["removed"] == 1
    assert [item["title"] for item in stored] == ["DevOps Engineer"]


def test_invalid_status_is_rejected(isolated_jobs):
    with pytest.raises(ValueError):
        jobs.update_status("missing", "hired")


def test_handle_reports_search_backend_failure(isolated_jobs, monkeypatch):
    monkeypatch.setattr(
        jobs,
        "discover",
        lambda _query: {
            "added": 0,
            "updated": 0,
            "total": 0,
            "searched": 0,
            "errors": ["connection refused"],
        },
    )
    observation, _recommendations = jobs.handle("buscame trabajo remoto")
    assert "SearXNG no está disponible" in observation.summary

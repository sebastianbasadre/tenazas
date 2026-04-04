#!/usr/bin/env python3
"""
Tests for jobs.py — Job search tracker.

Covers:
- NLP parsing (postulé, apliqué, oportunidad, vi puesto, me contactó)
- Seniority/source/salary detection
- Opportunity CRUD (add, get, list, update_field)
- Status machine (valid transitions, invalid transitions, terminal states)
- Pipeline summary
- Format output
- Edge cases (empty data, invalid input, missing fields)
- LLM analysis (mocked)

Corre: python3 tests/test_jobs.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import jobs


class _TempDirMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "JOBS_DIR": jobs.JOBS_DIR,
            "OPPORTUNITIES_FILE": jobs.OPPORTUNITIES_FILE,
            "PROFILE_FILE": jobs.PROFILE_FILE,
            "LOG_FILE": jobs.LOG_FILE,
        }
        jobs.JOBS_DIR = self.tmpdir / "jobs"
        jobs.OPPORTUNITIES_FILE = jobs.JOBS_DIR / "opportunities.json"
        jobs.PROFILE_FILE = jobs.JOBS_DIR / "profile.json"
        jobs.LOG_FILE = self.tmpdir / "logs" / "jobs.log"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(jobs, k, v)


# ── NLP Parsing ─────────────────────────────────────────────────


class TestParseOpportunity(unittest.TestCase):

    def test_postule_a_para(self):
        r = jobs.parse_opportunity("postulé a TIVIT para TAM")
        self.assertIsNotNone(r)
        self.assertEqual(r["company"], "TIVIT")
        self.assertEqual(r["role"], "TAM")

    def test_aplique_a_como(self):
        r = jobs.parse_opportunity("apliqué a Google como SRE Senior")
        self.assertIsNotNone(r)
        self.assertEqual(r["company"], "Google")
        self.assertIn("SRE", r["role"])

    def test_nueva_oportunidad(self):
        r = jobs.parse_opportunity("nueva oportunidad: MercadoLibre - Backend Dev")
        self.assertIsNotNone(r)
        self.assertEqual(r["company"], "MercadoLibre")
        self.assertEqual(r["role"], "Backend Dev")

    def test_vi_puesto(self):
        r = jobs.parse_opportunity("vi un puesto de DevOps en Globant")
        self.assertIsNotNone(r)
        self.assertEqual(r["company"], "Globant")
        self.assertEqual(r["role"], "DevOps")

    def test_me_contacto_recruiter(self):
        r = jobs.parse_opportunity("me contactó recruiter de Accenture para Java Lead")
        self.assertIsNotNone(r)
        self.assertEqual(r["company"], "Accenture")
        self.assertIn("Java Lead", r["role"])

    def test_unparseable(self):
        self.assertIsNone(jobs.parse_opportunity("hola que tal"))
        self.assertIsNone(jobs.parse_opportunity(""))

    def test_without_accent(self):
        r = jobs.parse_opportunity("postule a TIVIT para TAM")
        self.assertIsNotNone(r)

    def test_detects_seniority(self):
        r = jobs.parse_opportunity("postulé a Google como SRE Senior")
        self.assertEqual(r["seniority"], "senior")

    def test_detects_lead(self):
        r = jobs.parse_opportunity("oportunidad: Globant - Tech Lead")
        self.assertEqual(r["seniority"], "lead")


class TestDetectSeniority(unittest.TestCase):

    def test_senior(self):
        self.assertEqual(jobs._detect_seniority("Senior Developer"), "senior")

    def test_junior(self):
        self.assertEqual(jobs._detect_seniority("Junior QA"), "junior")

    def test_lead(self):
        self.assertEqual(jobs._detect_seniority("Tech Lead"), "lead")

    def test_none(self):
        self.assertIsNone(jobs._detect_seniority("Developer"))


class TestDetectSource(unittest.TestCase):

    def test_linkedin(self):
        self.assertEqual(jobs._detect_source("fuente: linkedin"), "linkedin")

    def test_referido(self):
        self.assertEqual(jobs._detect_source("via referido"), "referido")

    def test_keyword_in_text(self):
        self.assertEqual(jobs._detect_source("lo vi en linkedin"), "linkedin")

    def test_none(self):
        self.assertIsNone(jobs._detect_source("algo random"))


class TestDetectSalary(unittest.TestCase):

    def test_usd_range(self):
        r = jobs._detect_salary("salario: $3000-5000 usd")
        self.assertIsNotNone(r)
        self.assertIn("3000", r)

    def test_ars(self):
        r = jobs._detect_salary("sueldo: $1200000 ARS")
        self.assertIsNotNone(r)

    def test_no_salary(self):
        self.assertIsNone(jobs._detect_salary("sin info de plata"))


# ── Opportunity CRUD ────────────────────────────────────────────


class TestAddOpportunity(_TempDirMixin, unittest.TestCase):

    def test_add_basic(self):
        opp = jobs.add_opportunity("TIVIT", "TAM")
        self.assertEqual(opp["company"], "TIVIT")
        self.assertEqual(opp["role"], "TAM")
        self.assertEqual(opp["status"], "detected")
        self.assertIn("id", opp)
        self.assertIn("created_at", opp)
        self.assertIn("history", opp)

    def test_add_with_all_fields(self):
        opp = jobs.add_opportunity(
            "Google", "SRE", seniority="senior",
            source="linkedin", salary="$5000 USD",
            notes="referido por Juan",
        )
        self.assertEqual(opp["seniority"], "senior")
        self.assertEqual(opp["source"], "linkedin")
        self.assertEqual(opp["salary"], "$5000 USD")

    def test_persists(self):
        jobs.add_opportunity("Test", "Dev")
        opps = jobs._load_opportunities()
        self.assertEqual(len(opps), 1)

    def test_invalid_status_raises(self):
        with self.assertRaises(ValueError):
            jobs.add_opportunity("X", "Y", status="nonexistent")

    def test_invalid_seniority_saved_as_none(self):
        opp = jobs.add_opportunity("X", "Y", seniority="ultra")
        self.assertIsNone(opp["seniority"])

    def test_history_initialized(self):
        opp = jobs.add_opportunity("X", "Y")
        self.assertEqual(len(opp["history"]), 1)
        self.assertEqual(opp["history"][0]["status"], "detected")


class TestAddFromText(_TempDirMixin, unittest.TestCase):

    def test_postule_sets_applied(self):
        opp = jobs.add_from_text("postulé a TIVIT para TAM")
        self.assertIsNotNone(opp)
        self.assertEqual(opp["status"], "applied")

    def test_vi_puesto_sets_detected(self):
        opp = jobs.add_from_text("vi un puesto de SRE en Google")
        self.assertIsNotNone(opp)
        self.assertEqual(opp["status"], "detected")

    def test_me_contacto_sets_screening(self):
        opp = jobs.add_from_text("me contactó recruiter de Globant para Java Senior")
        self.assertIsNotNone(opp)
        self.assertEqual(opp["status"], "screening")

    def test_unparseable(self):
        self.assertIsNone(jobs.add_from_text("hola mundo"))


class TestGetOpportunity(_TempDirMixin, unittest.TestCase):

    def test_get_existing(self):
        opp = jobs.add_opportunity("TIVIT", "TAM")
        found = jobs.get_opportunity(opp["id"])
        self.assertIsNotNone(found)
        self.assertEqual(found["company"], "TIVIT")

    def test_get_nonexistent(self):
        self.assertIsNone(jobs.get_opportunity("nope"))


# ── Status Machine ──────────────────────────────────────────────


class TestStatusTransitions(_TempDirMixin, unittest.TestCase):

    def test_detected_to_interested(self):
        opp = jobs.add_opportunity("X", "Y")
        self.assertTrue(jobs.update_status(opp["id"], "interested"))
        self.assertEqual(jobs.get_opportunity(opp["id"])["status"], "interested")

    def test_detected_to_applied(self):
        opp = jobs.add_opportunity("X", "Y")
        self.assertTrue(jobs.update_status(opp["id"], "applied"))

    def test_applied_to_screening(self):
        opp = jobs.add_opportunity("X", "Y", status="applied")
        self.assertTrue(jobs.update_status(opp["id"], "screening"))

    def test_screening_to_interview(self):
        opp = jobs.add_opportunity("X", "Y", status="applied")
        jobs.update_status(opp["id"], "screening")
        self.assertTrue(jobs.update_status(opp["id"], "interview"))

    def test_interview_to_offer(self):
        opp = jobs.add_opportunity("X", "Y", status="applied")
        jobs.update_status(opp["id"], "screening")
        jobs.update_status(opp["id"], "interview")
        self.assertTrue(jobs.update_status(opp["id"], "offer"))

    def test_any_active_to_rejected(self):
        for initial in ("detected", "interested", "applied", "screening", "interview"):
            opp = jobs.add_opportunity("X", "Y", status=initial if initial != "screening" else "applied")
            if initial == "screening":
                jobs.update_status(opp["id"], "screening")
            self.assertTrue(jobs.update_status(opp["id"], "rejected"),
                            f"Failed: {initial} -> rejected")

    def test_rejected_is_terminal(self):
        opp = jobs.add_opportunity("X", "Y")
        jobs.update_status(opp["id"], "rejected")
        self.assertFalse(jobs.update_status(opp["id"], "interested"))

    def test_closed_is_terminal(self):
        opp = jobs.add_opportunity("X", "Y")
        jobs.update_status(opp["id"], "closed")
        self.assertFalse(jobs.update_status(opp["id"], "applied"))

    def test_cannot_skip_to_offer(self):
        opp = jobs.add_opportunity("X", "Y")
        self.assertFalse(jobs.update_status(opp["id"], "offer"))

    def test_invalid_status_fails(self):
        opp = jobs.add_opportunity("X", "Y")
        self.assertFalse(jobs.update_status(opp["id"], "nonexistent"))

    def test_nonexistent_id_fails(self):
        self.assertFalse(jobs.update_status("nope", "applied"))

    def test_update_appends_history(self):
        opp = jobs.add_opportunity("X", "Y")
        jobs.update_status(opp["id"], "interested", "looks good")
        updated = jobs.get_opportunity(opp["id"])
        self.assertEqual(len(updated["history"]), 2)
        self.assertEqual(updated["history"][-1]["status"], "interested")
        self.assertEqual(updated["history"][-1]["notes"], "looks good")

    def test_update_appends_notes(self):
        opp = jobs.add_opportunity("X", "Y")
        jobs.update_status(opp["id"], "interested", "me interesa")
        updated = jobs.get_opportunity(opp["id"])
        self.assertIn("me interesa", updated["notes"])


class TestUpdateField(_TempDirMixin, unittest.TestCase):

    def test_update_notes(self):
        opp = jobs.add_opportunity("X", "Y")
        self.assertTrue(jobs.update_field(opp["id"], "notes", "nueva nota"))
        self.assertEqual(jobs.get_opportunity(opp["id"])["notes"], "nueva nota")

    def test_update_salary(self):
        opp = jobs.add_opportunity("X", "Y")
        self.assertTrue(jobs.update_field(opp["id"], "salary", "$5000 USD"))

    def test_update_jd(self):
        opp = jobs.add_opportunity("X", "Y")
        self.assertTrue(jobs.update_field(opp["id"], "job_description", "Full JD text"))

    def test_disallowed_field(self):
        opp = jobs.add_opportunity("X", "Y")
        self.assertFalse(jobs.update_field(opp["id"], "status", "hacked"))
        self.assertFalse(jobs.update_field(opp["id"], "id", "new_id"))

    def test_nonexistent_id(self):
        self.assertFalse(jobs.update_field("nope", "notes", "test"))


# ── List & Pipeline ─────────────────────────────────────────────


class TestListOpportunities(_TempDirMixin, unittest.TestCase):

    def test_list_empty(self):
        self.assertEqual(jobs.list_opportunities(), [])

    def test_list_all(self):
        jobs.add_opportunity("A", "Dev")
        jobs.add_opportunity("B", "QA")
        self.assertEqual(len(jobs.list_opportunities()), 2)

    def test_list_by_status(self):
        jobs.add_opportunity("A", "Dev")
        opp2 = jobs.add_opportunity("B", "QA")
        jobs.update_status(opp2["id"], "rejected")
        active = jobs.list_opportunities(active_only=True)
        self.assertEqual(len(active), 1)

    def test_list_by_specific_status(self):
        jobs.add_opportunity("A", "Dev")
        jobs.add_opportunity("B", "QA", status="applied")
        self.assertEqual(len(jobs.list_opportunities(status="applied")), 1)


class TestPipelineSummary(_TempDirMixin, unittest.TestCase):

    def test_empty_pipeline(self):
        ps = jobs.pipeline_summary()
        self.assertEqual(ps["total"], 0)
        self.assertEqual(ps["active"], 0)

    def test_pipeline_counts(self):
        jobs.add_opportunity("A", "Dev")
        jobs.add_opportunity("B", "QA", status="applied")
        opp3 = jobs.add_opportunity("C", "PM")
        jobs.update_status(opp3["id"], "rejected")
        ps = jobs.pipeline_summary()
        self.assertEqual(ps["total"], 3)
        self.assertEqual(ps["active"], 2)
        self.assertIn("detected", ps["by_status"])
        self.assertIn("applied", ps["by_status"])

    def test_format_pipeline(self):
        jobs.add_opportunity("TIVIT", "TAM")
        ps = jobs.pipeline_summary()
        text = jobs.format_pipeline(ps)
        self.assertIn("Pipeline laboral", text)
        self.assertIn("TIVIT", text)

    def test_pipeline_has_required_keys(self):
        ps = jobs.pipeline_summary()
        for key in ("total", "active", "by_status", "recent"):
            self.assertIn(key, ps)


# ── LLM Analysis (mocked) ──────────────────────────────────────


class TestAnalyzeDescription(_TempDirMixin, unittest.TestCase):

    def test_empty_jd(self):
        result = jobs.analyze_description("")
        self.assertIn("error", result)

    @patch("runtime.jobs._call_ollama")
    def test_parses_valid_json(self, mock_ollama):
        mock_ollama.return_value = json.dumps({
            "title": "SRE Senior",
            "requirements": ["Python", "AWS"],
            "technologies": ["Docker", "K8s"],
            "summary": "Good role",
        })
        result = jobs.analyze_description("Looking for SRE with Python and AWS")
        self.assertEqual(result["title"], "SRE Senior")
        self.assertIn("Python", result["requirements"])

    @patch("runtime.jobs._call_ollama")
    def test_handles_no_response(self, mock_ollama):
        mock_ollama.return_value = ""
        result = jobs.analyze_description("some JD")
        self.assertIn("error", result)

    @patch("runtime.jobs._call_ollama")
    def test_handles_bad_json(self, mock_ollama):
        mock_ollama.return_value = "Here's my analysis in text form..."
        result = jobs.analyze_description("some JD")
        self.assertIn("raw", result)


class TestMatchScore(_TempDirMixin, unittest.TestCase):

    def test_missing_opportunity(self):
        result = jobs.match_score("nope")
        self.assertIn("error", result)

    def test_no_profile(self):
        opp = jobs.add_opportunity("X", "Y")
        result = jobs.match_score(opp["id"])
        self.assertIn("error", result)
        self.assertIn("no profile", result["error"])

    @patch("runtime.jobs._call_ollama")
    def test_with_profile(self, mock_ollama):
        mock_ollama.return_value = json.dumps({
            "score": 75,
            "strengths": ["Python"],
            "gaps": ["AWS cert"],
            "recommendation": "aplicar",
        })
        opp = jobs.add_opportunity("X", "Y")
        jobs.save_profile({"name": "Sebas", "skills": ["Python", "Docker"]})
        result = jobs.match_score(opp["id"])
        self.assertEqual(result["score"], 75)
        self.assertEqual(result["recommendation"], "aplicar")


class TestInterviewBrief(_TempDirMixin, unittest.TestCase):

    def test_missing_opportunity(self):
        result = jobs.interview_brief("nope")
        self.assertIn("no encontrada", result)

    @patch("runtime.jobs._call_ollama")
    def test_generates_brief(self, mock_ollama):
        mock_ollama.return_value = "# Brief\n- Point 1\n- Point 2"
        opp = jobs.add_opportunity("TIVIT", "TAM")
        result = jobs.interview_brief(opp["id"])
        self.assertIn("Brief", result)


# ── Edge Cases ──────────────────────────────────────────────────


class TestEdgeCases(_TempDirMixin, unittest.TestCase):

    def test_load_missing_file(self):
        self.assertEqual(jobs._load_opportunities(), [])

    def test_load_corrupt_json(self):
        jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
        jobs.OPPORTUNITIES_FILE.write_text("{broken")
        self.assertEqual(jobs._load_opportunities(), [])

    def test_load_empty_file(self):
        jobs.JOBS_DIR.mkdir(parents=True, exist_ok=True)
        jobs.OPPORTUNITIES_FILE.write_text("")
        self.assertEqual(jobs._load_opportunities(), [])

    def test_statuses_constant(self):
        self.assertEqual(len(jobs.STATUSES), 8)
        self.assertIn("detected", jobs.STATUSES)
        self.assertIn("offer", jobs.STATUSES)

    def test_all_statuses_in_transitions(self):
        for s in jobs.STATUSES:
            self.assertIn(s, jobs.TRANSITIONS)

    def test_terminal_states_empty_transitions(self):
        self.assertEqual(jobs.TRANSITIONS["rejected"], set())
        self.assertEqual(jobs.TRANSITIONS["closed"], set())

    def test_profile_roundtrip(self):
        jobs.save_profile({"name": "Test", "skills": ["Python"]})
        profile = jobs.load_profile()
        self.assertEqual(profile["name"], "Test")

    def test_profile_missing(self):
        self.assertEqual(jobs.load_profile(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)

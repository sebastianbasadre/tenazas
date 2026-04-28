#!/usr/bin/env python3
"""
Tests for operational_memory.py — Domain-specific fact store.

Covers:
- save_fact (valid, invalid domain/type, dedup, metadata, TTL)
- get_context (filter by domain, type, excludes expired)
- list_recent (limit, order)
- search_facts (keyword, cross-domain, domain filter)
- delete_fact (existing, not found)
- cleanup_expired (removes old, keeps active)
- get_stats (per domain counts)
- Edge cases (empty, corrupt file, cap per domain)

Corre: python3 tests/test_operational_memory.py
"""

import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/home/tenazas")
from runtime import operational_memory as om


class _TempDirMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "MEMORY_DIR": om.MEMORY_DIR,
            "MEMORY_FILE": om.MEMORY_FILE,
            "LOG_FILE": om.LOG_FILE,
        }
        om.MEMORY_DIR = self.tmpdir / "memory"
        om.MEMORY_FILE = om.MEMORY_DIR / "operational.json"
        om.LOG_FILE = self.tmpdir / "logs" / "operational_memory.log"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(om, k, v)


# ── Save Fact ───────────────────────────────────────────────────


class TestSaveFact(_TempDirMixin, unittest.TestCase):

    def test_save_basic(self):
        fact = om.save_fact("finance", "preference", "Presupuesto mensual 800k")
        self.assertIsNotNone(fact)
        self.assertEqual(fact["domain"], "finance")
        self.assertEqual(fact["type"], "preference")
        self.assertIn("created_at", fact)

    def test_save_with_metadata(self):
        fact = om.save_fact("finance", "threshold", "Alerta si super > 80k",
                            metadata={"category": "supermercado", "amount": 80000})
        self.assertEqual(fact["metadata"]["amount"], 80000)

    def test_save_with_source(self):
        fact = om.save_fact("jobs", "entity", "TIVIT", source="user")
        self.assertEqual(fact["source"], "user")

    def test_save_with_ttl(self):
        fact = om.save_fact("general", "context", "Buscando trabajo", ttl_days=30)
        self.assertIsNotNone(fact["expires_at"])

    def test_save_no_expiry(self):
        fact = om.save_fact("finance", "preference", "Algo permanente", ttl_days=0)
        self.assertIsNone(fact["expires_at"])

    def test_persists_to_file(self):
        om.save_fact("finance", "preference", "Test persist")
        data = json.loads(om.MEMORY_FILE.read_text())
        self.assertEqual(len(data["finance"]), 1)

    def test_invalid_domain(self):
        self.assertIsNone(om.save_fact("invalid", "preference", "test"))

    def test_invalid_type(self):
        self.assertIsNone(om.save_fact("finance", "invalid_type", "test"))

    def test_empty_content(self):
        self.assertIsNone(om.save_fact("finance", "preference", ""))
        self.assertIsNone(om.save_fact("finance", "preference", "  "))

    def test_dedup_same_content(self):
        om.save_fact("finance", "preference", "Presupuesto 800k")
        om.save_fact("finance", "preference", "Presupuesto 800k")
        data = json.loads(om.MEMORY_FILE.read_text())
        self.assertEqual(len(data["finance"]), 1)

    def test_dedup_updates_timestamp(self):
        om.save_fact("finance", "preference", "test dedup")
        import time
        time.sleep(0.01)
        fact = om.save_fact("finance", "preference", "test dedup")
        # updated_at should be refreshed
        self.assertIsNotNone(fact["updated_at"])

    def test_dedup_updates_metadata(self):
        om.save_fact("finance", "threshold", "alerta super", metadata={"amount": 50000})
        om.save_fact("finance", "threshold", "alerta super", metadata={"amount": 80000})
        facts = om.get_context("finance")
        self.assertEqual(facts[0]["metadata"]["amount"], 80000)

    def test_different_content_not_deduped(self):
        om.save_fact("finance", "preference", "fact A")
        om.save_fact("finance", "preference", "fact B")
        facts = om.get_context("finance")
        self.assertEqual(len(facts), 2)

    def test_cap_per_domain(self):
        for i in range(om.MAX_FACTS_PER_DOMAIN + 10):
            om.save_fact("finance", "note", f"note {i}")
        facts = om.get_context("finance")
        self.assertLessEqual(len(facts), om.MAX_FACTS_PER_DOMAIN)


# ── Get Context ─────────────────────────────────────────────────


class TestGetContext(_TempDirMixin, unittest.TestCase):

    def test_empty_domain(self):
        self.assertEqual(om.get_context("finance"), [])

    def test_returns_domain_facts(self):
        om.save_fact("finance", "preference", "A")
        om.save_fact("jobs", "entity", "B")
        facts = om.get_context("finance")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["content"], "A")

    def test_filter_by_type(self):
        om.save_fact("finance", "preference", "pref")
        om.save_fact("finance", "threshold", "thresh")
        facts = om.get_context("finance", fact_type="threshold")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["content"], "thresh")

    def test_excludes_expired(self):
        # Save with already-expired date
        mem = om._load_memory()
        mem["finance"] = [{
            "type": "note", "content": "old",
            "expires_at": "2020-01-01T00:00:00",
            "updated_at": "2020-01-01T00:00:00",
        }]
        om._save_memory(mem)
        facts = om.get_context("finance")
        self.assertEqual(len(facts), 0)

    def test_includes_non_expiring(self):
        mem = om._load_memory()
        mem["finance"] = [{
            "type": "preference", "content": "permanent",
            "expires_at": None,
            "updated_at": om.now().isoformat(),
        }]
        om._save_memory(mem)
        facts = om.get_context("finance")
        self.assertEqual(len(facts), 1)

    def test_newest_first(self):
        om.save_fact("finance", "note", "first")
        om.save_fact("finance", "note", "second")
        facts = om.get_context("finance")
        self.assertEqual(facts[0]["content"], "second")


# ── List Recent ─────────────────────────────────────────────────


class TestListRecent(_TempDirMixin, unittest.TestCase):

    def test_empty(self):
        self.assertEqual(om.list_recent("finance"), [])

    def test_respects_limit(self):
        for i in range(20):
            om.save_fact("finance", "note", f"note {i}")
        self.assertEqual(len(om.list_recent("finance", limit=5)), 5)

    def test_newest_first(self):
        om.save_fact("finance", "note", "old")
        om.save_fact("finance", "note", "new")
        recent = om.list_recent("finance", limit=1)
        self.assertEqual(recent[0]["content"], "new")


# ── Search ──────────────────────────────────────────────────────


class TestSearch(_TempDirMixin, unittest.TestCase):

    def test_search_in_content(self):
        om.save_fact("finance", "preference", "Presupuesto mensual 800k")
        om.save_fact("jobs", "entity", "TIVIT Argentina")
        results = om.search_facts("presupuesto")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["domain"], "finance")

    def test_search_in_metadata(self):
        om.save_fact("finance", "threshold", "alerta",
                     metadata={"category": "supermercado"})
        results = om.search_facts("supermercado")
        self.assertEqual(len(results), 1)

    def test_search_cross_domain(self):
        om.save_fact("finance", "note", "algo con TIVIT")
        om.save_fact("jobs", "entity", "TIVIT")
        results = om.search_facts("TIVIT")
        self.assertEqual(len(results), 2)

    def test_search_with_domain_filter(self):
        om.save_fact("finance", "note", "TIVIT pago")
        om.save_fact("jobs", "entity", "TIVIT")
        results = om.search_facts("TIVIT", domain="jobs")
        self.assertEqual(len(results), 1)

    def test_search_no_results(self):
        self.assertEqual(om.search_facts("nonexistent"), [])

    def test_search_case_insensitive(self):
        om.save_fact("jobs", "entity", "MercadoLibre")
        results = om.search_facts("mercadolibre")
        self.assertEqual(len(results), 1)


# ── Delete ──────────────────────────────────────────────────────


class TestDelete(_TempDirMixin, unittest.TestCase):

    def test_delete_existing(self):
        om.save_fact("finance", "note", "to delete")
        self.assertTrue(om.delete_fact("finance", "to delete"))
        self.assertEqual(len(om.get_context("finance")), 0)

    def test_delete_not_found(self):
        self.assertFalse(om.delete_fact("finance", "nonexistent"))

    def test_delete_persists(self):
        om.save_fact("finance", "note", "keep")
        om.save_fact("finance", "note", "remove")
        om.delete_fact("finance", "remove")
        facts = om.get_context("finance")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["content"], "keep")


# ── Cleanup ─────────────────────────────────────────────────────


class TestCleanup(_TempDirMixin, unittest.TestCase):

    def test_removes_expired(self):
        mem = {"finance": [
            {"type": "note", "content": "old", "expires_at": "2020-01-01T00:00:00",
             "updated_at": "2020-01-01"},
            {"type": "note", "content": "active", "expires_at": None,
             "updated_at": om.now().isoformat()},
        ]}
        om._save_memory(mem)
        removed = om.cleanup_expired()
        self.assertEqual(removed, 1)
        facts = om.get_context("finance")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["content"], "active")

    def test_cleanup_nothing_expired(self):
        om.save_fact("finance", "note", "fresh")
        self.assertEqual(om.cleanup_expired(), 0)


# ── Stats ───────────────────────────────────────────────────────


class TestStats(_TempDirMixin, unittest.TestCase):

    def test_empty_stats(self):
        s = om.get_stats()
        self.assertEqual(s["total"], 0)

    def test_stats_reflect_data(self):
        om.save_fact("finance", "note", "a")
        om.save_fact("finance", "note", "b")
        om.save_fact("jobs", "entity", "c")
        s = om.get_stats()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["finance"], 2)
        self.assertEqual(s["jobs"], 1)


# ── Edge Cases ──────────────────────────────────────────────────


class TestEdgeCases(_TempDirMixin, unittest.TestCase):

    def test_load_missing_file(self):
        self.assertEqual(om._load_memory(), {})

    def test_load_corrupt_file(self):
        om.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        om.MEMORY_FILE.write_text("{broken")
        self.assertEqual(om._load_memory(), {})

    def test_domains_constant(self):
        self.assertIn("finance", om.VALID_DOMAINS)
        self.assertIn("expense", om.VALID_DOMAINS)
        self.assertIn("jobs", om.VALID_DOMAINS)

    def test_fact_types_constant(self):
        self.assertIn("preference", om.VALID_FACT_TYPES)
        self.assertIn("threshold", om.VALID_FACT_TYPES)
        self.assertIn("entity", om.VALID_FACT_TYPES)


if __name__ == "__main__":
    unittest.main(verbosity=2)

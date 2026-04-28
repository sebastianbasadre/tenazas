#!/usr/bin/env python3
"""
Tests for shared_memory.py — Multi-type memory system.

Covers:
- load/save memory (missing file, valid data, max entries cap)
- add_entry() for all 4 types + invalid type
- query_memory() with keyword filtering and limit
- clear_working_memory()
- ingest_episodic() from memory_log.json
- ingest_procedural() from SKILL.md files
- get_memory_stats()
- JSON helpers (load_json, save_json via runtime_utils)

Corre: python3 tests/test_shared_memory.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import shared_memory as sm


class _TempMemoryMixin:
    """Redirects all file paths to a temp directory."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_memory = sm.MEMORY_FILE
        self._orig_log = sm.LOG_FILE
        self._orig_memlog = sm.MEMORY_LOG_FILE
        self._orig_skills = sm.SKILLS_DIR
        sm.MEMORY_FILE = self.tmpdir / "shared_memory.json"
        sm.LOG_FILE = self.tmpdir / "logs" / "shared_memory.log"
        sm.MEMORY_LOG_FILE = self.tmpdir / "memory_log.json"
        sm.SKILLS_DIR = self.tmpdir / "skills"

    def tearDown(self):
        sm.MEMORY_FILE = self._orig_memory
        sm.LOG_FILE = self._orig_log
        sm.MEMORY_LOG_FILE = self._orig_memlog
        sm.SKILLS_DIR = self._orig_skills


class TestLoadMemory(_TempMemoryMixin, unittest.TestCase):
    """Loading memory from disk."""

    def test_missing_file_returns_empty_types(self):
        data = sm.load_memory()
        for mtype in sm.MEMORY_TYPES:
            self.assertIn(mtype, data)
            self.assertEqual(data[mtype], [])

    def test_existing_data_loaded(self):
        sm.MEMORY_FILE.write_text(json.dumps({
            "semantic": [{"content": "test"}],
            "episodic": [], "procedural": [], "working": [],
        }))
        data = sm.load_memory()
        self.assertEqual(len(data["semantic"]), 1)

    def test_missing_types_initialized(self):
        sm.MEMORY_FILE.write_text(json.dumps({"semantic": []}))
        data = sm.load_memory()
        self.assertIn("episodic", data)
        self.assertIn("working", data)


class TestSaveMemory(_TempMemoryMixin, unittest.TestCase):
    """Saving memory to disk with max cap."""

    def test_save_creates_file(self):
        sm.save_memory({"semantic": [], "episodic": [], "procedural": [], "working": []})
        self.assertTrue(sm.MEMORY_FILE.exists())

    def test_max_entries_enforced(self):
        data = {
            "semantic": [{"content": f"entry-{i}"} for i in range(150)],
            "episodic": [], "procedural": [], "working": [],
        }
        sm.save_memory(data)
        loaded = json.loads(sm.MEMORY_FILE.read_text())
        self.assertEqual(len(loaded["semantic"]), sm.MAX_ENTRIES_PER_TYPE)

    def test_keeps_most_recent(self):
        data = {
            "semantic": [{"content": f"entry-{i}"} for i in range(150)],
            "episodic": [], "procedural": [], "working": [],
        }
        sm.save_memory(data)
        loaded = json.loads(sm.MEMORY_FILE.read_text())
        # Should keep last 100 (entries 50-149)
        self.assertEqual(loaded["semantic"][0]["content"], "entry-50")
        self.assertEqual(loaded["semantic"][-1]["content"], "entry-149")


class TestAddEntry(_TempMemoryMixin, unittest.TestCase):
    """Adding entries to memory."""

    def test_add_semantic(self):
        result = sm.add_entry("semantic", "NAS IP is 192.168.2.2", "CLAUDE.md")
        self.assertTrue(result)
        data = sm.load_memory()
        self.assertEqual(len(data["semantic"]), 1)
        self.assertEqual(data["semantic"][0]["content"], "NAS IP is 192.168.2.2")

    def test_add_episodic(self):
        self.assertTrue(sm.add_entry("episodic", "container_down — sonarr"))

    def test_add_procedural(self):
        self.assertTrue(sm.add_entry("procedural", "Skill: weather"))

    def test_add_working(self):
        self.assertTrue(sm.add_entry("working", "current task context"))

    def test_invalid_type_rejected(self):
        result = sm.add_entry("invalid_type", "test")
        self.assertFalse(result)

    def test_entry_has_timestamp(self):
        sm.add_entry("semantic", "test")
        data = sm.load_memory()
        self.assertIn("ts", data["semantic"][0])

    def test_entry_has_source(self):
        sm.add_entry("semantic", "test", "my_source")
        data = sm.load_memory()
        self.assertEqual(data["semantic"][0]["source"], "my_source")

    def test_metadata_stored(self):
        sm.add_entry("semantic", "test", metadata={"score": 0.9})
        data = sm.load_memory()
        self.assertEqual(data["semantic"][0]["metadata"]["score"], 0.9)

    def test_multiple_entries_appended(self):
        sm.add_entry("semantic", "first")
        sm.add_entry("semantic", "second")
        data = sm.load_memory()
        self.assertEqual(len(data["semantic"]), 2)


class TestQueryMemory(_TempMemoryMixin, unittest.TestCase):
    """Querying memory with filters."""

    def setUp(self):
        super().setUp()
        # Seed some data
        sm.add_entry("semantic", "NAS IP is 192.168.2.2", "infra")
        sm.add_entry("semantic", "VPN server in Spain", "vpn")
        sm.add_entry("semantic", "ChromaDB on port 8100", "rag")

    def test_query_all(self):
        results = sm.query_memory("semantic")
        self.assertEqual(len(results), 3)

    def test_query_with_keyword(self):
        results = sm.query_memory("semantic", keyword="VPN")
        self.assertEqual(len(results), 1)
        self.assertIn("VPN", results[0]["content"])

    def test_keyword_case_insensitive(self):
        results = sm.query_memory("semantic", keyword="nas")
        self.assertEqual(len(results), 1)

    def test_keyword_matches_source(self):
        results = sm.query_memory("semantic", keyword="rag")
        self.assertEqual(len(results), 1)

    def test_no_match_returns_empty(self):
        results = sm.query_memory("semantic", keyword="nonexistent")
        self.assertEqual(results, [])

    def test_limit_respected(self):
        results = sm.query_memory("semantic", limit=2)
        self.assertEqual(len(results), 2)

    def test_most_recent_first(self):
        results = sm.query_memory("semantic")
        # Last added should be first in results
        self.assertIn("ChromaDB", results[0]["content"])

    def test_invalid_type_returns_empty(self):
        results = sm.query_memory("invalid")
        self.assertEqual(results, [])

    def test_empty_type_returns_empty(self):
        results = sm.query_memory("working")
        self.assertEqual(results, [])


class TestClearWorkingMemory(_TempMemoryMixin, unittest.TestCase):
    """Clearing working memory preserves other types."""

    def test_clears_working(self):
        sm.add_entry("working", "temp context")
        sm.add_entry("semantic", "permanent fact")
        sm.clear_working_memory()
        data = sm.load_memory()
        self.assertEqual(len(data["working"]), 0)
        self.assertEqual(len(data["semantic"]), 1)


class TestIngestEpisodic(_TempMemoryMixin, unittest.TestCase):
    """Importing events from memory_log.json."""

    def test_ingest_from_log(self):
        log_data = [
            {"ts": "2026-03-30T10:00:00", "events": [
                {"type": "container_down", "container": "sonarr"},
            ]},
            {"ts": "2026-03-30T10:05:00", "events": [
                {"type": "vpn_leak"},
            ]},
        ]
        sm.MEMORY_LOG_FILE.write_text(json.dumps(log_data))
        sm.ingest_episodic()
        data = sm.load_memory()
        self.assertEqual(len(data["episodic"]), 2)

    def test_no_duplicates(self):
        log_data = [
            {"ts": "2026-03-30T10:00:00", "events": [{"type": "test"}]},
        ]
        sm.MEMORY_LOG_FILE.write_text(json.dumps(log_data))
        sm.ingest_episodic()
        sm.ingest_episodic()  # Run again
        data = sm.load_memory()
        self.assertEqual(len(data["episodic"]), 1)

    def test_missing_log_no_error(self):
        sm.ingest_episodic()  # No memory_log.json
        data = sm.load_memory()
        self.assertEqual(len(data["episodic"]), 0)

    def test_non_list_log_no_error(self):
        sm.MEMORY_LOG_FILE.write_text(json.dumps({"not": "a list"}))
        sm.ingest_episodic()  # Should not crash

    def test_event_content_includes_container(self):
        log_data = [
            {"ts": "2026-03-30T10:00:00", "events": [
                {"type": "container_down", "container": "radarr"},
            ]},
        ]
        sm.MEMORY_LOG_FILE.write_text(json.dumps(log_data))
        sm.ingest_episodic()
        data = sm.load_memory()
        self.assertIn("radarr", data["episodic"][0]["content"])


class TestIngestProcedural(_TempMemoryMixin, unittest.TestCase):
    """Scanning SKILL.md files into procedural memory."""

    def test_ingest_skills(self):
        # Create fake skill dirs
        for name in ["weather", "tts", "rag"]:
            skill_dir = sm.SKILLS_DIR / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(f"# {name}")

        sm.ingest_procedural()
        data = sm.load_memory()
        self.assertEqual(len(data["procedural"]), 3)

    def test_no_duplicates(self):
        skill_dir = sm.SKILLS_DIR / "weather"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# weather")

        sm.ingest_procedural()
        sm.ingest_procedural()  # Again
        data = sm.load_memory()
        self.assertEqual(len(data["procedural"]), 1)

    def test_missing_skills_dir_no_error(self):
        sm.ingest_procedural()  # No skills dir
        data = sm.load_memory()
        self.assertEqual(len(data["procedural"]), 0)

    def test_dir_without_skill_md_skipped(self):
        (sm.SKILLS_DIR / "empty_skill").mkdir(parents=True)
        sm.ingest_procedural()
        data = sm.load_memory()
        self.assertEqual(len(data["procedural"]), 0)


class TestGetMemoryStats(_TempMemoryMixin, unittest.TestCase):
    """Memory statistics."""

    def test_empty_stats(self):
        stats = sm.get_memory_stats()
        for mtype in sm.MEMORY_TYPES:
            self.assertEqual(stats[mtype], 0)

    def test_stats_reflect_entries(self):
        sm.add_entry("semantic", "fact 1")
        sm.add_entry("semantic", "fact 2")
        sm.add_entry("working", "context")
        stats = sm.get_memory_stats()
        self.assertEqual(stats["semantic"], 2)
        self.assertEqual(stats["working"], 1)
        self.assertEqual(stats["episodic"], 0)


class TestJsonHelpers(_TempMemoryMixin, unittest.TestCase):
    """load_json and save_json (from runtime_utils)."""

    def test_load_missing_returns_default(self):
        result = sm.load_json(self.tmpdir / "nope.json", [])
        self.assertEqual(result, [])

    def test_load_corrupt_returns_default(self):
        bad_file = self.tmpdir / "bad.json"
        bad_file.write_text("{broken")
        result = sm.load_json(bad_file, {})
        self.assertEqual(result, {})

    def test_save_and_load_roundtrip(self):
        path = self.tmpdir / "test.json"
        sm.save_json(path, {"key": "value"})
        result = sm.load_json(path)
        self.assertEqual(result["key"], "value")


if __name__ == "__main__":
    unittest.main()

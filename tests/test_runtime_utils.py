#!/usr/bin/env python3
"""
Tests para runtime_utils.py — load_json, save_json, now_iso, write_atomic.
Corre: python3 tests/test_runtime_utils.py
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

# Agregar el path del runtime al sys.path
sys.path.insert(0, "/home/tenazas")
from runtime.runtime_utils import load_json, save_json, now_iso, write_atomic


class TestLoadJson(unittest.TestCase):
    """Tests for load_json()."""

    def test_load_json_valid(self):
        """Loads valid JSON file, returns parsed content."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "data.json"
            p.write_text('{"key": "value", "num": 42}')
            result = load_json(p)
            self.assertEqual(result, {"key": "value", "num": 42})

    def test_load_json_missing(self):
        """Missing file returns empty dict when default is None."""
        result = load_json("/nonexistent/path/data.json")
        self.assertEqual(result, {})

    def test_load_json_corrupt(self):
        """Invalid JSON returns default."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.json"
            p.write_text("{invalid json content")
            result = load_json(p)
            self.assertEqual(result, {})

    def test_load_json_custom_default(self):
        """Custom default returned on error."""
        result = load_json("/nonexistent/file.json", default=[])
        self.assertEqual(result, [])

        result = load_json("/nonexistent/file.json", default={"fallback": True})
        self.assertEqual(result, {"fallback": True})


class TestSaveJson(unittest.TestCase):
    """Tests for save_json()."""

    def test_save_json_creates_dirs(self):
        """Saves to nested path, creates parent dirs."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "nested" / "deep" / "data.json"
            save_json(p, {"test": True})
            self.assertTrue(p.exists())
            content = json.loads(p.read_text())
            self.assertEqual(content, {"test": True})

    def test_save_json_content(self):
        """Saved file has indent=2, no ascii escaping, datetime serialized."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.json"
            ts = datetime(2026, 4, 28, 12, 0, 0)
            save_json(p, {"msg": "hola mundo", "ts": ts})
            raw = p.read_text()
            # indent=2: should have newlines and spaces
            self.assertIn("\n", raw)
            self.assertIn("  ", raw)
            # ensure_ascii=False: unicode preserved
            self.assertIn("hola mundo", raw)
            # default=str: datetime serialized as string
            self.assertIn("2026-04-28", raw)


class TestNowIso(unittest.TestCase):
    """Tests for now_iso()."""

    def test_now_iso_format(self):
        """Returns string matching ISO8601 pattern."""
        result = now_iso()
        self.assertIsInstance(result, str)
        # Contains date-time separator
        self.assertIn("T", result)
        # Parseable by fromisoformat
        parsed = datetime.fromisoformat(result)
        self.assertIsInstance(parsed, datetime)
        # Has timezone info (astimezone was called)
        self.assertIsNotNone(parsed.tzinfo)


class TestWriteAtomic(unittest.TestCase):
    """Tests for write_atomic()."""

    def test_write_atomic_success(self):
        """Writes file atomically, content correct."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            write_atomic(p, {"status": "ok", "count": 7})
            content = json.loads(p.read_text())
            self.assertEqual(content, {"status": "ok", "count": 7})

    def test_write_atomic_creates_dirs(self):
        """Creates parent dirs if needed."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "dir" / "state.json"
            write_atomic(p, {"nested": True})
            self.assertTrue(p.exists())
            content = json.loads(p.read_text())
            self.assertEqual(content, {"nested": True})

    def test_write_atomic_no_partial(self):
        """If error during write, target file unchanged."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            # Write initial content
            p.write_text('{"original": true}')
            # Create object whose __str__ raises, defeating default=str
            class Unserializable:
                def __repr__(self):
                    raise ValueError("cannot serialize")
                def __str__(self):
                    raise ValueError("cannot serialize")
            with self.assertRaises((TypeError, ValueError)):
                write_atomic(p, {"bad": Unserializable()})
            # Original file should be intact
            content = json.loads(p.read_text())
            self.assertEqual(content, {"original": True})

    def test_write_atomic_cleanup(self):
        """Temp file removed on error."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            class Unserializable:
                def __str__(self):
                    raise ValueError("boom")
            try:
                write_atomic(p, {"bad": Unserializable()})
            except (TypeError, ValueError):
                pass
            # No .tmp files left behind
            tmp_files = list(Path(d).glob("*.tmp"))
            self.assertEqual(tmp_files, [])


if __name__ == "__main__":
    unittest.main()

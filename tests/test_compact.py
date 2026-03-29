#!/usr/bin/env python3
"""
Tests para skills/compact-context/compact.py
Corre: python3 tests/test_compact.py
"""

import sys
import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from io import StringIO
from datetime import datetime

sys.path.insert(0, "/home/tenazas/.openclaw/workspace/skills/compact-context")
import compact as c


class TestSaveCompact(unittest.TestCase):
    """save_compact() debe escribir en el archivo de memoria del día."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_memory = c.MEMORY_DIR
        c.MEMORY_DIR = self.tmpdir

    def tearDown(self):
        c.MEMORY_DIR = self.orig_memory

    def test_creates_memory_file(self):
        c.save_compact("**Estado actual:** todo OK")
        today = datetime.now().strftime("%Y-%m-%d")
        expected_file = os.path.join(self.tmpdir, f"{today}.md")
        self.assertTrue(os.path.exists(expected_file))

    def test_file_contains_summary(self):
        summary = "**Estado actual:** gluetun OK\n**Pendiente:** configurar cron"
        filepath = c.save_compact(summary)
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("gluetun OK", content)
        self.assertIn("configurar cron", content)

    def test_file_contains_timestamp_header(self):
        filepath = c.save_compact("test summary")
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Contexto", content)  # parte ASCII-safe del header

    def test_multiple_compactions_append(self):
        c.save_compact("primera compactacion")
        filepath = c.save_compact("segunda compactacion")
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("primera compactacion", content)
        self.assertIn("segunda compactacion", content)

    def test_returns_filepath(self):
        filepath = c.save_compact("test")
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertIn(today, filepath)
        self.assertTrue(filepath.endswith(".md"))


class TestGenerateSummaryFromLogs(unittest.TestCase):
    """generate_summary_from_logs() debe llamar a Ollama y retornar texto."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_memory = c.MEMORY_DIR
        c.MEMORY_DIR = self.tmpdir

    def tearDown(self):
        c.MEMORY_DIR = self.orig_memory

    @patch("compact.urlopen")
    def test_returns_ollama_response(self, mock_urlopen):
        fake_response = json.dumps({
            "response": "**Estado actual:** todo OK\n**Pendiente:** nada"
        }).encode()
        mock_urlopen.return_value = MagicMock(read=lambda: fake_response)

        result = c.generate_summary_from_logs()
        self.assertIn("Estado actual", result)

    @patch("compact.urlopen")
    def test_ollama_failure_returns_error_message(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("connection refused")

        result = c.generate_summary_from_logs()
        # Chequeamos por string ASCII-safe para evitar problemas de encoding en Windows
        self.assertIn("connection refused", result.lower())

    @patch("compact.urlopen")
    def test_reads_existing_memory_file(self, mock_urlopen):
        # Crear un memory file con contenido
        today = datetime.now().strftime("%Y-%m-%d")
        memory_file = os.path.join(self.tmpdir, f"{today}.md")
        with open(memory_file, "w") as f:
            f.write("## 10:00\nSe reinició gluetun exitosamente.")

        fake_response = json.dumps({"response": "resumen generado"}).encode()
        mock_urlopen.return_value = MagicMock(read=lambda: fake_response)

        # Verificar que el prompt incluye el contenido del log
        captured_payload = {}
        original_urlopen = c.urlopen

        def capture_urlopen(req, timeout=None):
            captured_payload["data"] = json.loads(req.data.decode())
            return MagicMock(read=lambda: fake_response)

        with patch("compact.urlopen", side_effect=capture_urlopen):
            c.generate_summary_from_logs()

        self.assertIn("gluetun", captured_payload["data"]["prompt"])

    @patch("compact.urlopen")
    def test_uses_compact_model(self, mock_urlopen):
        fake_response = json.dumps({"response": "ok"}).encode()
        captured = {}

        def capture(req, timeout=None):
            captured["payload"] = json.loads(req.data.decode())
            return MagicMock(read=lambda: fake_response)

        with patch("compact.urlopen", side_effect=capture):
            c.generate_summary_from_logs()

        self.assertEqual(captured["payload"]["model"], c.COMPACT_MODEL)


class TestPollutionSignals(unittest.TestCase):
    """La lista de señales de contexto sucio debe existir y tener al menos 3 ítems."""

    def test_signals_exist(self):
        self.assertIsInstance(c.POLLUTION_SIGNALS, list)
        self.assertGreaterEqual(len(c.POLLUTION_SIGNALS), 3)


class TestAnchorOutput(unittest.TestCase):
    """print_anchor() debe imprimir el separador y el texto del resumen."""

    def test_anchor_contains_summary(self):
        summary = "**Estado actual:** radarr caído\n**Pendiente:** revisar logs"
        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            c.print_anchor("/tmp/test.md", summary)
            output = mock_stdout.getvalue()

        self.assertIn("radarr caído", output)
        self.assertIn("CONTEXTO COMPACTADO", output)
        self.assertIn("ANCHOR", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)

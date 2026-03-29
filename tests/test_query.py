#!/usr/bin/env python3
"""
Tests para skills/rag/query.py
Corre: python3 tests/test_query.py
"""

import sys
import json
import unittest
from unittest.mock import patch, MagicMock
from io import StringIO

# Agregar el path del skill al sys.path
sys.path.insert(0, "/home/tenazas/.openclaw/workspace/skills/rag")
import query as q


class TestSourceBoost(unittest.TestCase):
    """Lógica de boost por tipo de fuente — pura, sin HTTP."""

    def test_skill_md_gets_boost(self):
        self.assertEqual(q.source_boost("skills/infra-monitor/SKILL.md"), 0.05)
        self.assertEqual(q.source_boost("skills/rag/SKILL.md"), 0.05)

    def test_workspace_root_md_gets_boost(self):
        self.assertEqual(q.source_boost("SOUL.md"), 0.05)
        self.assertEqual(q.source_boost("AGENTS.md"), 0.05)
        self.assertEqual(q.source_boost("IDENTITY.md"), 0.05)

    def test_memory_log_gets_penalty(self):
        self.assertEqual(q.source_boost("memory/2026-03-25.md"), -0.03)
        self.assertEqual(q.source_boost("memory/2026-03-29.md"), -0.03)

    def test_nested_non_skill_no_boost(self):
        # Archivo .md en subdirectorio que no es skills/ ni memory/
        self.assertEqual(q.source_boost("subdir/somefile.md"), 0.0)

    def test_non_md_no_boost(self):
        self.assertEqual(q.source_boost("skills/rag/ingest.py"), 0.0)
        self.assertEqual(q.source_boost("scripts/deploy.sh"), 0.0)

    def test_skill_without_skill_md_no_boost(self):
        # Solo SKILL.md — no otros .md dentro de skills/
        self.assertEqual(q.source_boost("skills/rag/notes.md"), 0.0)


class TestMinScore(unittest.TestCase):
    """El umbral mínimo debe ser 0.6."""

    def test_min_score_value(self):
        self.assertEqual(q.MIN_SCORE, 0.6)


class TestKeywordBoost(unittest.TestCase):
    """Keyword boost proporcional."""

    def test_all_terms_match(self):
        boost = q.keyword_boost("gluetun vpn proxy", ["gluetun", "vpn"])
        self.assertAlmostEqual(boost, 0.15, places=2)

    def test_no_terms_match(self):
        boost = q.keyword_boost("algo completamente distinto", ["gluetun", "vpn"])
        self.assertEqual(boost, 0.0)

    def test_partial_match(self):
        boost = q.keyword_boost("gluetun proxy", ["gluetun", "vpn"])
        self.assertAlmostEqual(boost, 0.075, places=2)

    def test_empty_terms(self):
        boost = q.keyword_boost("texto cualquiera", [])
        self.assertEqual(boost, 0.0)


class TestScoreFiltering(unittest.TestCase):
    """Resultados con score < 0.6 deben ser descartados."""

    def _make_chroma_response(self, items):
        """Construye una respuesta fake de ChromaDB.
        items: list de (text, source, distance)
        """
        return {
            "ids": [[f"id{i}" for i in range(len(items))]],
            "documents": [[item[0] for item in items]],
            "metadatas": [[{"source": item[1]} for item in items]],
            "distances": [[item[2] for item in items]],
        }

    @patch("query.requests.post")
    @patch("query.requests.get")
    def test_low_score_results_filtered(self, mock_get, mock_post):
        # Simular colección ID
        mock_get.return_value = MagicMock(json=lambda: {"id": "fake-collection-id"})

        # Simular: embedding request + query request
        embedding_resp = MagicMock()
        embedding_resp.json.return_value = {"embedding": [0.1] * 768}

        # distance 0.7 → base_score = 0.3 → con boosts no llega a 0.6
        chroma_data = self._make_chroma_response([
            ("texto irrelevante", "memory/2026-03-01.md", 0.7),   # score ~0.27
            ("texto relevante sobre gluetun vpn", "skills/infra-monitor/SKILL.md", 0.3),  # score ~0.75
        ])
        chroma_resp = MagicMock()
        chroma_resp.json.return_value = chroma_data

        mock_post.side_effect = [embedding_resp, chroma_resp]

        results = q.search("gluetun vpn", top_k=5)

        # Solo el resultado relevante debe pasar el filtro
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "skills/infra-monitor/SKILL.md")
        self.assertGreaterEqual(results[0]["score"], q.MIN_SCORE)

    @patch("query.requests.post")
    @patch("query.requests.get")
    def test_all_low_scores_returns_empty(self, mock_get, mock_post):
        mock_get.return_value = MagicMock(json=lambda: {"id": "fake-id"})

        embedding_resp = MagicMock()
        embedding_resp.json.return_value = {"embedding": [0.1] * 768}

        # Todos con distancia alta → score bajo
        chroma_data = self._make_chroma_response([
            ("resultado pobre 1", "memory/2026-03-01.md", 0.8),
            ("resultado pobre 2", "memory/2026-03-02.md", 0.75),
        ])
        chroma_resp = MagicMock()
        chroma_resp.json.return_value = chroma_data

        mock_post.side_effect = [embedding_resp, chroma_resp]

        results = q.search("query sin contexto", top_k=5)
        self.assertEqual(results, [])

    @patch("query.requests.post")
    @patch("query.requests.get")
    def test_skill_ranks_above_memory_same_distance(self, mock_get, mock_post):
        """Con la misma distancia vectorial, SKILL.md debe rankear > memory/*.md."""
        mock_get.return_value = MagicMock(json=lambda: {"id": "fake-id"})

        embedding_resp = MagicMock()
        embedding_resp.json.return_value = {"embedding": [0.1] * 768}

        # Misma distancia → source_boost decide
        chroma_data = self._make_chroma_response([
            ("info sobre infra monitor containers", "memory/2026-03-28.md", 0.25),
            ("info sobre infra monitor containers", "skills/infra-monitor/SKILL.md", 0.25),
        ])
        chroma_resp = MagicMock()
        chroma_resp.json.return_value = chroma_data

        mock_post.side_effect = [embedding_resp, chroma_resp]

        results = q.search("infra monitor containers", top_k=5)

        self.assertGreater(len(results), 0)
        # El primero debe ser el SKILL.md
        self.assertEqual(results[0]["source"], "skills/infra-monitor/SKILL.md")


class TestJsonOutput(unittest.TestCase):
    """Con --json y sin resultados, debe devolver []."""

    @patch("query.requests.post")
    @patch("query.requests.get")
    def test_empty_json_output(self, mock_get, mock_post):
        mock_get.return_value = MagicMock(json=lambda: {"id": "fake-id"})

        embedding_resp = MagicMock()
        embedding_resp.json.return_value = {"embedding": [0.1] * 768}

        chroma_data = {
            "ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]
        }
        chroma_resp = MagicMock()
        chroma_resp.json.return_value = chroma_data

        mock_post.side_effect = [embedding_resp, chroma_resp]

        results = q.search("nada", top_k=5)
        self.assertEqual(results, [])
        # Debe serializar a JSON válido
        self.assertEqual(json.dumps(results), "[]")


if __name__ == "__main__":
    unittest.main(verbosity=2)

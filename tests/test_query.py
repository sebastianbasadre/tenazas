#!/usr/bin/env python3
"""
Tests para skills/rag/query.py — full pipeline.
Corre: python3 tests/test_query.py
"""

import sys
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

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

    def test_memory_log_gets_penalty(self):
        self.assertEqual(q.source_boost("memory/2026-03-25.md"), -0.03)

    def test_nested_non_skill_no_boost(self):
        self.assertEqual(q.source_boost("subdir/somefile.md"), 0.0)

    def test_non_md_no_boost(self):
        self.assertEqual(q.source_boost("skills/rag/ingest.py"), 0.0)

    def test_skill_without_skill_md_no_boost(self):
        self.assertEqual(q.source_boost("skills/rag/notes.md"), 0.0)


class TestRecencyBoost(unittest.TestCase):
    """Recent memory files get extra boost."""

    def test_today_gets_max_boost(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(q._recency_boost(f"memory/{today}-session.md"), 0.04)

    def test_yesterday_gets_boost(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertEqual(q._recency_boost(f"memory/{yesterday}-notes.md"), 0.04)

    def test_three_days_ago(self):
        d = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        self.assertEqual(q._recency_boost(f"memory/{d}-notes.md"), 0.02)

    def test_week_old(self):
        d = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
        self.assertEqual(q._recency_boost(f"memory/{d}-notes.md"), 0.01)

    def test_old_no_boost(self):
        self.assertEqual(q._recency_boost("memory/2025-01-01-old.md"), 0.0)

    def test_non_memory_no_boost(self):
        self.assertEqual(q._recency_boost("SOUL.md"), 0.0)

    def test_no_date_in_name_no_boost(self):
        self.assertEqual(q._recency_boost("memory/learnings.md"), 0.0)


class TestMinScore(unittest.TestCase):
    def test_min_score_value(self):
        self.assertEqual(q.MIN_SCORE, 0.6)


class TestContextCompression(unittest.TestCase):
    """Deduplication and truncation."""

    def test_deduplicates_similar_chunks(self):
        results = [
            {"text": "gluetun vpn proxy config spain", "source": "a.md", "score": 0.8},
            {"text": "gluetun vpn proxy config spain extra", "source": "b.md", "score": 0.7},
        ]
        compressed = q.compress_context(results, top_k=5)
        self.assertEqual(len(compressed), 1)
        self.assertEqual(compressed[0]["source"], "a.md")

    def test_keeps_different_chunks(self):
        results = [
            {"text": "gluetun vpn proxy config", "source": "a.md", "score": 0.8},
            {"text": "ollama notebook gpu model inference", "source": "b.md", "score": 0.7},
        ]
        compressed = q.compress_context(results, top_k=5)
        self.assertEqual(len(compressed), 2)

    def test_respects_top_k(self):
        # Each chunk must have truly different words to avoid dedup
        topics = [
            "gluetun vpn proxy spain nordvpn",
            "ollama notebook gpu inference rtx",
            "sonarr radarr lidarr media management",
            "chromadb vector embeddings semantic search",
            "telegram bot notification messaging api",
            "hue bridge philips smart lighting control",
            "nmap network scanner device discovery",
            "whisper transcription speech recognition audio",
            "piper tts voice synthesis engine",
            "docker compose container orchestration yaml",
        ]
        results = [
            {"text": topics[i], "source": f"{i}.md", "score": 0.9 - i * 0.02}
            for i in range(10)
        ]
        compressed = q.compress_context(results, top_k=3)
        self.assertEqual(len(compressed), 3)

    def test_truncates_long_chunks(self):
        results = [
            {"text": "x" * 3000, "source": "a.md", "score": 0.9},
            {"text": "y" * 3000, "source": "b.md", "score": 0.8},
        ]
        compressed = q.compress_context(results, top_k=2, max_chars=2000)
        for r in compressed:
            self.assertLessEqual(len(r["text"]), 1004)  # budget + "..."


class TestMemoryFusion(unittest.TestCase):
    """Memory type classification and fusion."""

    def test_classifies_skill_as_procedural(self):
        results = [{"source": "skills/rag/SKILL.md", "text": "how to query", "score": 0.8}]
        fused = q.memory_fusion(results, "query")
        self.assertEqual(fused[0]["memory_type"], "procedural")

    def test_classifies_soul_as_semantic(self):
        results = [{"source": "SOUL.md", "text": "identity", "score": 0.8}]
        fused = q.memory_fusion(results, "identity")
        self.assertEqual(fused[0]["memory_type"], "semantic")

    def test_classifies_memory_as_episodic(self):
        results = [{"source": "memory/2026-03-29.md", "text": "session log", "score": 0.8}]
        fused = q.memory_fusion(results, "log")
        self.assertEqual(fused[0]["memory_type"], "episodic")

    def test_working_memory_injected_first(self):
        results = [{"source": "SOUL.md", "text": "identity", "score": 0.8}]
        fused = q.memory_fusion(results, "test", working_context="current task info")
        self.assertEqual(fused[0]["memory_type"], "working")
        self.assertEqual(fused[0]["source"], "working_memory")
        self.assertGreater(fused[0]["score"], fused[1]["score"])

    def test_no_working_memory_if_empty(self):
        results = [{"source": "SOUL.md", "text": "identity", "score": 0.8}]
        fused = q.memory_fusion(results, "test", working_context="")
        self.assertFalse(any(r["source"] == "working_memory" for r in fused))

    def test_episodic_memory_loaded_from_disk(self):
        tmpdir = tempfile.mkdtemp()
        today = datetime.now().strftime("%Y-%m-%d")
        with open(os.path.join(tmpdir, f"{today}-session.md"), "w") as f:
            f.write("hoy hice deploy de gluetun y reinicié sonarr")

        with patch.object(q, "MEMORY_DIR", tmpdir):
            results = [{"source": "SOUL.md", "text": "identity", "score": 0.8}]
            fused = q.memory_fusion(results, "gluetun deploy")

        episodic = [r for r in fused if r["memory_type"] == "episodic"]
        self.assertGreater(len(episodic), 0)


class TestQueryRewrite(unittest.TestCase):
    """Query rewrite with LLM fallback."""

    def test_short_query_no_rewrite(self):
        """Short queries skip LLM call."""
        qr = q.query_rewrite("vpn status")
        self.assertEqual(qr["original"], "vpn status")
        self.assertEqual(qr["rewritten"], "vpn status")

    @patch("query._ollama_available", return_value=False)
    def test_fallback_when_ollama_offline(self, _):
        qr = q.query_rewrite("como configuro las luces del hue bridge")
        self.assertEqual(qr["rewritten"], qr["original"])
        self.assertIsInstance(qr["keywords"], list)

    @patch("query._ollama_generate")
    @patch("query._ollama_available", return_value=True)
    def test_successful_rewrite(self, _, mock_gen):
        mock_gen.return_value = json.dumps({
            "rewritten": "configuración Hue Bridge luces",
            "keywords": ["hue", "bridge", "luces", "configuración"],
            "sub_queries": [],
        })
        qr = q.query_rewrite("como configuro las luces del hue bridge")
        self.assertEqual(qr["rewritten"], "configuración Hue Bridge luces")
        self.assertIn("hue", qr["keywords"])

    @patch("query._ollama_generate", return_value=None)
    @patch("query._ollama_available", return_value=True)
    def test_rewrite_llm_failure_fallback(self, _, __):
        qr = q.query_rewrite("como configuro las luces del hue bridge")
        self.assertEqual(qr["rewritten"], qr["original"])


class TestReranking(unittest.TestCase):
    """Full reranking with all signals."""

    def test_combines_all_signals(self):
        results = [
            {
                "id": "1", "text": "gluetun vpn config",
                "source": "skills/infra-monitor/SKILL.md",
                "vector_score": 0.7, "keyword_score": 0.1,
                "multi_query_boost": 0.03,
            },
            {
                "id": "2", "text": "random other content",
                "source": "memory/2025-01-01.md",
                "vector_score": 0.75, "keyword_score": 0.0,
            },
        ]
        ranked = q.rerank(results)
        # SKILL.md with boosts should outrank memory with higher vector score
        self.assertEqual(ranked[0]["source"], "skills/infra-monitor/SKILL.md")

    def test_filters_below_min_score(self):
        results = [
            {"id": "1", "text": "low", "source": "x.md", "vector_score": 0.3},
        ]
        ranked = q.rerank(results)
        self.assertEqual(ranked, [])


class TestScoreFiltering(unittest.TestCase):
    """End-to-end search with mocked ChromaDB."""

    def setUp(self):
        self._empty_dir = tempfile.mkdtemp()
        self._orig_memory_dir = q.MEMORY_DIR
        q.MEMORY_DIR = self._empty_dir

    def tearDown(self):
        q.MEMORY_DIR = self._orig_memory_dir

    def _make_chroma_response(self, items):
        return {
            "ids": [[f"id{i}" for i in range(len(items))]],
            "documents": [[item[0] for item in items]],
            "metadatas": [[{"source": item[1]} for item in items]],
            "distances": [[item[2] for item in items]],
        }

    @patch("query._ollama_available", return_value=False)
    @patch("query.requests.post")
    @patch("query.requests.get")
    def test_low_score_results_filtered(self, mock_get, mock_post, _):
        mock_get.return_value = MagicMock(json=lambda: {"id": "fake-id"})

        embedding_resp = MagicMock()
        embedding_resp.json.return_value = {"embedding": [0.1] * 768}

        chroma_data = self._make_chroma_response([
            ("texto irrelevante", "memory/2026-03-01.md", 0.7),
            ("texto relevante sobre gluetun vpn", "skills/infra-monitor/SKILL.md", 0.3),
        ])
        chroma_resp = MagicMock()
        chroma_resp.json.return_value = chroma_data
        chroma_resp.raise_for_status = MagicMock()
        embedding_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [embedding_resp, chroma_resp]

        pipeline = q.search("gluetun vpn", top_k=5)
        results = pipeline["results"]

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "skills/infra-monitor/SKILL.md")
        self.assertGreaterEqual(results[0]["score"], q.MIN_SCORE)
        # Confidence and self_check must be present
        self.assertIn("confidence", pipeline)
        self.assertIn("self_check", pipeline)

    @patch("query._ollama_available", return_value=False)
    @patch("query.requests.post")
    @patch("query.requests.get")
    def test_all_low_scores_returns_empty(self, mock_get, mock_post, _):
        mock_get.return_value = MagicMock(json=lambda: {"id": "fake-id"})

        embedding_resp = MagicMock()
        embedding_resp.json.return_value = {"embedding": [0.1] * 768}
        embedding_resp.raise_for_status = MagicMock()

        chroma_data = self._make_chroma_response([
            ("resultado pobre 1", "memory/2026-03-01.md", 0.8),
            ("resultado pobre 2", "memory/2026-03-02.md", 0.75),
        ])
        chroma_resp = MagicMock()
        chroma_resp.json.return_value = chroma_data
        chroma_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [embedding_resp, chroma_resp]

        pipeline = q.search("query sin contexto", top_k=5)
        results = pipeline["results"]
        self.assertEqual(results, [])

    @patch("query._ollama_available", return_value=False)
    @patch("query.requests.post")
    @patch("query.requests.get")
    def test_skill_ranks_above_memory_same_distance(self, mock_get, mock_post, _):
        mock_get.return_value = MagicMock(json=lambda: {"id": "fake-id"})

        embedding_resp = MagicMock()
        embedding_resp.json.return_value = {"embedding": [0.1] * 768}
        embedding_resp.raise_for_status = MagicMock()

        chroma_data = self._make_chroma_response([
            ("info sobre infra monitor containers", "memory/2026-03-28.md", 0.25),
            ("info sobre infra monitor containers", "skills/infra-monitor/SKILL.md", 0.25),
        ])
        chroma_resp = MagicMock()
        chroma_resp.json.return_value = chroma_data
        chroma_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [embedding_resp, chroma_resp]

        pipeline = q.search("infra monitor containers", top_k=5)
        results = pipeline["results"]

        # After dedup, only one survives (they have identical text)
        # The SKILL.md one should be the survivor (higher score)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["source"], "skills/infra-monitor/SKILL.md")


class TestJsonOutput(unittest.TestCase):

    def setUp(self):
        self._empty_dir = tempfile.mkdtemp()
        self._orig_memory_dir = q.MEMORY_DIR
        q.MEMORY_DIR = self._empty_dir

    def tearDown(self):
        q.MEMORY_DIR = self._orig_memory_dir

    @patch("query._ollama_available", return_value=False)
    @patch("query.requests.post")
    @patch("query.requests.get")
    def test_empty_json_output(self, mock_get, mock_post, _):
        mock_get.return_value = MagicMock(json=lambda: {"id": "fake-id"})

        embedding_resp = MagicMock()
        embedding_resp.json.return_value = {"embedding": [0.1] * 768}
        embedding_resp.raise_for_status = MagicMock()

        chroma_data = {
            "ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]
        }
        chroma_resp = MagicMock()
        chroma_resp.json.return_value = chroma_data
        chroma_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [embedding_resp, chroma_resp]

        pipeline = q.search("nada", top_k=5)
        results = pipeline["results"]
        self.assertEqual(results, [])
        self.assertIn("confidence", pipeline)


if __name__ == "__main__":
    unittest.main(verbosity=2)

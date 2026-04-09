#!/usr/bin/env python3
"""
test_eval_harness.py — Tests for multitool eval harness.
Corre: python3 tests/test_eval_harness.py
"""

import unittest
import json
import tempfile
import os
import sys

# eval_metrics and split_dataset live in tool_tuning/
sys.path.insert(0, "/home/tenazas")
sys.path.insert(0, "/home/tenazas/runtime/tool_tuning")
from eval_metrics import precision, recall, f1, exact_match, aggregate_metrics


class TestPrecision(unittest.TestCase):
    def test_perfect(self):
        self.assertEqual(precision({"a", "b"}, {"a", "b"}), 1.0)

    def test_zero(self):
        self.assertEqual(precision({"c"}, {"a", "b"}), 0.0)

    def test_partial(self):
        self.assertAlmostEqual(precision({"a", "b", "c"}, {"a", "b"}), 2 / 3)

    def test_empty_predicted_empty_expected(self):
        self.assertEqual(precision(set(), set()), 1.0)

    def test_empty_predicted_nonempty_expected(self):
        self.assertEqual(precision(set(), {"a"}), 0.0)

    def test_nonempty_predicted_empty_expected(self):
        self.assertEqual(precision({"a"}, set()), 0.0)


class TestRecall(unittest.TestCase):
    def test_perfect(self):
        self.assertEqual(recall({"a", "b"}, {"a", "b"}), 1.0)

    def test_zero(self):
        self.assertEqual(recall({"c"}, {"a", "b"}), 0.0)

    def test_partial(self):
        self.assertAlmostEqual(recall({"a"}, {"a", "b"}), 0.5)

    def test_empty_both(self):
        self.assertEqual(recall(set(), set()), 1.0)

    def test_empty_predicted(self):
        self.assertEqual(recall(set(), {"a"}), 0.0)

    def test_empty_expected(self):
        self.assertEqual(recall({"a"}, set()), 0.0)


class TestF1(unittest.TestCase):
    def test_perfect(self):
        self.assertEqual(f1({"a"}, {"a"}), 1.0)

    def test_zero(self):
        self.assertEqual(f1({"a"}, {"b"}), 0.0)

    def test_partial(self):
        score = f1({"a", "b"}, {"a", "c"})
        self.assertAlmostEqual(score, 0.5)

    def test_symmetry(self):
        self.assertEqual(f1({"a"}, {"a"}), f1({"a"}, {"a"}))


class TestExactMatch(unittest.TestCase):
    def test_equal(self):
        self.assertTrue(exact_match({"a", "b"}, {"a", "b"}))

    def test_not_equal(self):
        self.assertFalse(exact_match({"a"}, {"a", "b"}))

    def test_empty_both(self):
        self.assertTrue(exact_match(set(), set()))

    def test_one_empty(self):
        self.assertFalse(exact_match({"a"}, set()))


class TestAggregateMetrics(unittest.TestCase):
    VALID_TOOLS = {"weather", "calendar", "email", "smart_home", "tts"}

    def _make_results(self, cases):
        return [
            {"query": f"q{i}", "predicted": set(p), "expected": set(e)}
            for i, (p, e) in enumerate(cases)
        ]

    def test_perfect_batch(self):
        results = self._make_results([
            (["weather"], ["weather"]),
            (["calendar", "email"], ["calendar", "email"]),
        ])
        m = aggregate_metrics(results, self.VALID_TOOLS)
        self.assertEqual(m["exact_match"], 1.0)
        self.assertEqual(m["precision"], 1.0)
        self.assertEqual(m["recall"], 1.0)
        self.assertEqual(m["f1"], 1.0)

    def test_hallucination_detected(self):
        results = self._make_results([
            (["weather", "FAKE_TOOL"], ["weather"]),
        ])
        m = aggregate_metrics(results, self.VALID_TOOLS)
        self.assertGreater(m["hallucination_rate"], 0)

    def test_no_hallucination(self):
        results = self._make_results([
            (["weather"], ["weather"]),
        ])
        m = aggregate_metrics(results, self.VALID_TOOLS)
        self.assertEqual(m["hallucination_rate"], 0.0)

    def test_over_trigger(self):
        results = self._make_results([
            (["weather"], []),
        ])
        m = aggregate_metrics(results, self.VALID_TOOLS)
        self.assertEqual(m["over_trigger_rate"], 1.0)

    def test_under_trigger(self):
        results = self._make_results([
            ([], ["weather"]),
        ])
        m = aggregate_metrics(results, self.VALID_TOOLS)
        self.assertEqual(m["under_trigger_rate"], 1.0)

    def test_per_tool_f1_populated(self):
        results = self._make_results([
            (["weather"], ["weather"]),
            (["calendar"], ["calendar"]),
        ])
        m = aggregate_metrics(results, self.VALID_TOOLS)
        self.assertIn("weather", m["per_tool_f1"])
        self.assertIn("calendar", m["per_tool_f1"])
        self.assertEqual(m["per_tool_f1"]["weather"], 1.0)

    def test_empty_results(self):
        m = aggregate_metrics([], self.VALID_TOOLS)
        self.assertIn("error", m)

    def test_mixed_batch(self):
        results = self._make_results([
            (["weather"], ["weather"]),
            (["calendar"], ["email"]),
            (["smart_home", "tts"], ["tts"]),
            ([], ["email"]),
        ])
        m = aggregate_metrics(results, self.VALID_TOOLS)
        self.assertEqual(m["n"], 4)
        self.assertLess(m["exact_match"], 1.0)
        self.assertGreater(m["under_trigger_rate"], 0)

    def test_multitool_recall(self):
        results = self._make_results([
            (["weather"], ["weather", "calendar"]),
        ])
        m = aggregate_metrics(results, self.VALID_TOOLS)
        self.assertAlmostEqual(m["recall"], 0.5)


class TestSplitDataset(unittest.TestCase):

    def _make_temp_jsonl(self, n: int) -> str:
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            for i in range(n):
                f.write(json.dumps({"messages": [
                    {"role": "user", "content": f"query {i}"},
                    {"role": "assistant", "content": "<tools><tool>weather</tool></tools>"}
                ]}) + "\n")
        return path

    def test_split_ratio(self):
        from split_dataset import split_dataset

        input_path = self._make_temp_jsonl(100)
        train_path = input_path + ".train"
        eval_path = input_path + ".eval"

        try:
            stats = split_dataset(input_path, train_path, eval_path,
                                  eval_ratio=0.2, seed=42)
            self.assertEqual(stats["total"], 100)
            self.assertEqual(stats["eval"], 20)
            self.assertEqual(stats["train"], 80)
        finally:
            for p in [input_path, train_path, eval_path]:
                if os.path.exists(p):
                    os.unlink(p)

    def test_split_reproducible(self):
        from split_dataset import split_dataset

        input_path = self._make_temp_jsonl(50)
        t1, e1 = input_path + ".t1", input_path + ".e1"
        t2, e2 = input_path + ".t2", input_path + ".e2"

        try:
            split_dataset(input_path, t1, e1, seed=42)
            split_dataset(input_path, t2, e2, seed=42)

            eval1 = [json.loads(l) for l in open(e1)]
            eval2 = [json.loads(l) for l in open(e2)]
            self.assertEqual(eval1, eval2)
        finally:
            for p in [input_path, t1, e1, t2, e2]:
                if os.path.exists(p):
                    os.unlink(p)

    def test_split_different_seeds(self):
        from split_dataset import split_dataset

        input_path = self._make_temp_jsonl(50)
        t1, e1 = input_path + ".t1", input_path + ".e1"
        t2, e2 = input_path + ".t2", input_path + ".e2"

        try:
            split_dataset(input_path, t1, e1, seed=42)
            split_dataset(input_path, t2, e2, seed=99)

            eval1 = [json.loads(l) for l in open(e1)]
            eval2 = [json.loads(l) for l in open(e2)]
            self.assertNotEqual(eval1, eval2)
        finally:
            for p in [input_path, t1, e1, t2, e2]:
                if os.path.exists(p):
                    os.unlink(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)

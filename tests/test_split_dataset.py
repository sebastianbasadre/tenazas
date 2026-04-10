#!/usr/bin/env python3
"""
test_split_dataset.py — Tests for split_dataset.py normalize_example.

Verifies that all input formats produce valid messages output.
"""

import sys
import os
import json
import tempfile
import unittest

sys.path.insert(0, "/home/tenazas/runtime/tool_tuning")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workspace", "runtime", "tool_tuning"))

from split_dataset import normalize_example, split_dataset


class TestNormalizeExample(unittest.TestCase):

    def _assert_valid_messages(self, result):
        """Assert result has valid messages format."""
        self.assertIn("messages", result)
        msgs = result["messages"]
        self.assertIsInstance(msgs, list)
        self.assertGreaterEqual(len(msgs), 2)
        roles = [m["role"] for m in msgs]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)

    def test_messages_passthrough(self):
        entry = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hola"},
                {"role": "assistant", "content": "<tools></tools>"},
            ]
        }
        result = normalize_example(entry)
        self.assertEqual(result, entry)

    def test_input_label_single_tool(self):
        entry = {"input": "escanea la red", "label": "network_scan"}
        result = normalize_example(entry)
        self._assert_valid_messages(result)
        assistant_msg = result["messages"][-1]["content"]
        self.assertIn("<tool>network_scan</tool>", assistant_msg)

    def test_input_label_none(self):
        entry = {"input": "hola", "label": "none"}
        result = normalize_example(entry)
        self._assert_valid_messages(result)
        assistant_msg = result["messages"][-1]["content"]
        self.assertEqual(assistant_msg, "<tools></tools>")

    def test_input_tools_multi(self):
        entry = {"input": "clima y agenda", "tools": ["weather", "calendar"]}
        result = normalize_example(entry)
        self._assert_valid_messages(result)
        assistant_msg = result["messages"][-1]["content"]
        self.assertIn("<tool>weather</tool>", assistant_msg)
        self.assertIn("<tool>calendar</tool>", assistant_msg)

    def test_input_tools_empty(self):
        entry = {"input": "hola", "tools": []}
        result = normalize_example(entry)
        self._assert_valid_messages(result)
        assistant_msg = result["messages"][-1]["content"]
        self.assertEqual(assistant_msg, "<tools></tools>")

    def test_extra_fields_preserved_in_messages(self):
        entry = {
            "input": "nmap",
            "label": "network_scan",
            "source": "real",
            "difficulty": "easy",
        }
        result = normalize_example(entry)
        self._assert_valid_messages(result)
        # Extra fields don't break anything
        self.assertIn("<tool>network_scan</tool>", result["messages"][-1]["content"])

    def test_system_message_present(self):
        entry = {"input": "test", "label": "weather"}
        result = normalize_example(entry)
        system_msgs = [m for m in result["messages"] if m["role"] == "system"]
        self.assertEqual(len(system_msgs), 1)
        self.assertIn("XML", system_msgs[0]["content"])


class TestSplitDatasetNormalization(unittest.TestCase):
    """Test that split_dataset normalizes all examples in output."""

    def test_mixed_formats_all_normalized(self):
        lines = [
            json.dumps({"input": "escanea la red", "label": "network_scan"}),
            json.dumps({"input": "clima y agenda", "tools": ["weather", "calendar"]}),
            json.dumps({"messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hola"},
                {"role": "assistant", "content": "<tools></tools>"},
            ]}),
            json.dumps({"input": "hola", "label": "none"}),
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines))
            input_path = f.name

        train_path = input_path + ".train"
        eval_path = input_path + ".eval"

        try:
            stats = split_dataset(input_path, train_path, eval_path, eval_ratio=0.25, seed=42)
            self.assertEqual(stats["total"], 4)

            # Check ALL output lines have "messages"
            for path in [train_path, eval_path]:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        entry = json.loads(line)
                        self.assertIn("messages", entry,
                                      f"Missing 'messages' in output: {line[:80]}")
                        msgs = entry["messages"]
                        roles = [m["role"] for m in msgs]
                        self.assertIn("assistant", roles)
        finally:
            for p in [input_path, train_path, eval_path]:
                try:
                    os.unlink(p)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)

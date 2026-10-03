import json
import unittest
from unittest.mock import patch

from workspace.runtime import homelab_goals as hg


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()


class TestGemmaWarm(unittest.TestCase):
    def test_ok_when_gemma_loaded_in_vram(self):
        payload = {"models": [{"name": "gemma3:12b", "size_vram": 8074129571}]}
        with patch.object(hg, "urlopen", return_value=_Response(payload)):
            status, evidence = hg._eval_gemma_warm()

        self.assertEqual(status, "ok")
        self.assertIn("loaded in VRAM", evidence)

    def test_warn_when_gemma_loaded_without_vram(self):
        payload = {"models": [{"name": "gemma3:12b", "size_vram": 0}]}
        with patch.object(hg, "urlopen", return_value=_Response(payload)):
            status, evidence = hg._eval_gemma_warm()

        self.assertEqual(status, "warn")
        self.assertEqual(evidence, "gemma3:12b loaded but not in VRAM")

    def test_warn_when_gemma_not_loaded(self):
        payload = {"models": [{"name": "qwen3:8b", "size_vram": 5225388164}]}
        with patch.object(hg, "urlopen", return_value=_Response(payload)):
            status, evidence = hg._eval_gemma_warm()

        self.assertEqual(status, "warn")
        self.assertIn("qwen3:8b", evidence)


if __name__ == "__main__":
    unittest.main()

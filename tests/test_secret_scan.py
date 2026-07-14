import unittest
from pathlib import Path

from tools import secret_scan


class SecretScanTests(unittest.TestCase):
    def test_detects_private_key_marker(self):
        line = "x = '-----BEGIN OPENSSH PRIVATE KEY-----'"
        self.assertTrue(any(pattern.search(line) for pattern in secret_scan.PATTERNS))

    def test_ignores_env_var_reference(self):
        self.assertTrue(secret_scan._looks_like_env_reference("OPENROUTER_API_KEY"))
        self.assertTrue(secret_scan._looks_like_env_reference("${GOOGLE_API_KEY}"))

    def test_assignment_pattern_detects_literal(self):
        line = 'api_key = "abc123abc123abc123abc123"'
        match = secret_scan.ASSIGNMENT_PATTERN.search(line)
        self.assertIsNotNone(match)
        self.assertFalse(secret_scan._looks_like_env_reference(match.group(2)))

    def test_allows_example_files(self):
        self.assertFalse(secret_scan.should_scan(Path(".env.example")))


if __name__ == "__main__":
    unittest.main()

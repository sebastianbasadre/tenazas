#!/usr/bin/env python3
"""
Tests for workspace/runtime/runtime_config.py — centralized infrastructure config.
Covers: default values, env var overrides, helper functions, existing API, patchability.

Run: python tests/test_runtime_config.py
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from runtime.runtime_config import (
        CONFIG_PATH, load_config, get_telegram_settings, get_telegram_bot_token,
        get_allowed_chat_ids, get_default_telegram_chat_id, get_hue_api_key,
        validate_config, get_config_value,
        _env_or_default, _cfg_get,
        LAN_SUBNET, ROUTER_IP, NAS_IP, NAS_BACKUP_IP, HOMELAB_IP, HOMELAB_USER,
        OLLAMA_GPU_URL, OLLAMA_GPU_TAGS_URL, OLLAMA_GPU_GENERATE_URL,
        OLLAMA_GPU_CHAT_URL, OLLAMA_NAS_URL,
        CHROMADB_URL, PROMETHEUS_URL, GRAFANA_URL, GLUETUN_PROXY, SEARXNG_URL,
        HUE_BRIDGE_IP, SAMSUNG_M8_IP, SAMSUNG_M8_MAC, SAMSUNG_Q70_IP,
        SAMSUNG_Q70_MAC, MARANTZ_IP, HUE_SYNC_BOX_IP, NEST_DOORBELL_IP,
        XIAOMI_VACUUM_IP, SHIELD_TV_IP, NEST_MINI_IP, HOME_MINI_IP, DECO_IP,
        HOMELAB_GPU_EXPORTER_PORT, HOMELAB_WINDOWS_EXPORTER_PORT,
    )
except ImportError:
    from workspace.runtime.runtime_config import (
        CONFIG_PATH, load_config, get_telegram_settings, get_telegram_bot_token,
        get_allowed_chat_ids, get_default_telegram_chat_id, get_hue_api_key,
        validate_config, get_config_value,
        _env_or_default, _cfg_get,
        LAN_SUBNET, ROUTER_IP, NAS_IP, NAS_BACKUP_IP, HOMELAB_IP, HOMELAB_USER,
        OLLAMA_GPU_URL, OLLAMA_GPU_TAGS_URL, OLLAMA_GPU_GENERATE_URL,
        OLLAMA_GPU_CHAT_URL, OLLAMA_NAS_URL,
        CHROMADB_URL, PROMETHEUS_URL, GRAFANA_URL, GLUETUN_PROXY, SEARXNG_URL,
        HUE_BRIDGE_IP, SAMSUNG_M8_IP, SAMSUNG_M8_MAC, SAMSUNG_Q70_IP,
        SAMSUNG_Q70_MAC, MARANTZ_IP, HUE_SYNC_BOX_IP, NEST_DOORBELL_IP,
        XIAOMI_VACUUM_IP, SHIELD_TV_IP, NEST_MINI_IP, HOME_MINI_IP, DECO_IP,
        HOMELAB_GPU_EXPORTER_PORT, HOMELAB_WINDOWS_EXPORTER_PORT,
    )


class TestDefaultValues(unittest.TestCase):
    """Verify all constants have correct default values."""

    def test_network_defaults(self):
        self.assertEqual(LAN_SUBNET, "192.168.2.0/24")
        self.assertEqual(ROUTER_IP, "192.168.2.1")
        self.assertEqual(NAS_IP, "192.168.2.2")
        self.assertEqual(NAS_BACKUP_IP, "192.168.2.3")
        self.assertEqual(HOMELAB_IP, "192.168.2.6")
        self.assertEqual(HOMELAB_USER, "sbasadre")

    def test_ollama_defaults(self):
        self.assertEqual(OLLAMA_GPU_URL, "http://192.168.2.6:11434")
        self.assertEqual(OLLAMA_GPU_TAGS_URL, "http://192.168.2.6:11434/api/tags")
        self.assertEqual(OLLAMA_GPU_GENERATE_URL, "http://192.168.2.6:11434/api/generate")
        self.assertEqual(OLLAMA_GPU_CHAT_URL, "http://192.168.2.6:11434/api/chat")
        self.assertEqual(OLLAMA_NAS_URL, "http://127.0.0.1:11434")

    def test_nas_services_defaults(self):
        self.assertEqual(CHROMADB_URL, "http://192.168.2.2:8100")
        self.assertEqual(PROMETHEUS_URL, "http://192.168.2.2:9090")
        self.assertEqual(GRAFANA_URL, "http://192.168.2.2:3000")
        self.assertEqual(GLUETUN_PROXY, "http://192.168.2.2:8888")
        self.assertEqual(SEARXNG_URL, "http://192.168.2.2:8890")

    def test_smart_home_defaults(self):
        self.assertEqual(HUE_BRIDGE_IP, "192.168.2.11")
        self.assertEqual(SAMSUNG_M8_IP, "192.168.2.46")
        self.assertEqual(SAMSUNG_M8_MAC, "BC:45:5B:9F:F3:2A")
        self.assertEqual(SAMSUNG_Q70_IP, "192.168.2.29")
        self.assertEqual(SAMSUNG_Q70_MAC, "B0:E4:5C:20:38:60")
        self.assertEqual(MARANTZ_IP, "192.168.2.80")
        self.assertEqual(HUE_SYNC_BOX_IP, "192.168.2.36")
        self.assertEqual(NEST_DOORBELL_IP, "192.168.2.22")
        self.assertEqual(XIAOMI_VACUUM_IP, "192.168.2.99")
        self.assertEqual(SHIELD_TV_IP, "192.168.2.96")
        self.assertEqual(NEST_MINI_IP, "192.168.2.62")
        self.assertEqual(HOME_MINI_IP, "192.168.2.83")
        self.assertEqual(DECO_IP, "192.168.2.30")

    def test_exporter_ports(self):
        self.assertEqual(HOMELAB_GPU_EXPORTER_PORT, 9835)
        self.assertEqual(HOMELAB_WINDOWS_EXPORTER_PORT, 9182)


class TestEnvOrDefault(unittest.TestCase):
    """Test the _env_or_default helper function directly."""

    def test_returns_default_when_env_not_set(self):
        result = _env_or_default("TENAZAS_NONEXISTENT_KEY_XYZ", "fallback")
        self.assertEqual(result, "fallback")

    @patch.dict(os.environ, {"TENAZAS_TEST_KEY": "override_value"})
    def test_returns_env_when_set(self):
        result = _env_or_default("TENAZAS_TEST_KEY", "fallback")
        self.assertEqual(result, "override_value")

    @patch.dict(os.environ, {"TENAZAS_TEST_KEY": "  "})
    def test_returns_default_when_env_is_whitespace(self):
        result = _env_or_default("TENAZAS_TEST_KEY", "fallback")
        self.assertEqual(result, "fallback")

    @patch.dict(os.environ, {"TENAZAS_TEST_KEY": ""})
    def test_returns_default_when_env_is_empty_string(self):
        result = _env_or_default("TENAZAS_TEST_KEY", "fallback")
        self.assertEqual(result, "fallback")

    @patch.dict(os.environ, {"TENAZAS_TEST_KEY": " trimmed "})
    def test_strips_whitespace_from_env(self):
        result = _env_or_default("TENAZAS_TEST_KEY", "fallback")
        self.assertEqual(result, "trimmed")


class TestCfgGet(unittest.TestCase):
    """Test the _cfg_get helper that checks env > json path > default."""

    @patch.dict(os.environ, {"TENAZAS_TEST_CFG": "from_env"})
    def test_env_takes_priority_over_json(self):
        result = _cfg_get(["some", "path"], "TENAZAS_TEST_CFG", "default")
        self.assertEqual(result, "from_env")

    @patch("workspace.runtime.runtime_config.load_config")
    def test_walks_json_path(self, mock_load):
        mock_load.return_value = {"level1": {"level2": "json_value"}}
        # Ensure env var is not set
        env = {k: v for k, v in os.environ.items() if k != "TENAZAS_CFG_WALK_TEST"}
        with patch.dict(os.environ, env, clear=True):
            result = _cfg_get(["level1", "level2"], "TENAZAS_CFG_WALK_TEST", "default")
        self.assertEqual(result, "json_value")

    @patch("workspace.runtime.runtime_config.load_config")
    def test_returns_default_when_json_path_missing(self, mock_load):
        mock_load.return_value = {"other": "data"}
        env = {k: v for k, v in os.environ.items() if k != "TENAZAS_CFG_MISS_TEST"}
        with patch.dict(os.environ, env, clear=True):
            result = _cfg_get(["nonexist", "deep"], "TENAZAS_CFG_MISS_TEST", "fallback")
        self.assertEqual(result, "fallback")


class TestDerivedUrls(unittest.TestCase):
    """Derived URLs should reference base constants."""

    def test_tags_url_derived_from_gpu_url(self):
        self.assertTrue(OLLAMA_GPU_TAGS_URL.startswith(OLLAMA_GPU_URL))
        self.assertEqual(OLLAMA_GPU_TAGS_URL, f"{OLLAMA_GPU_URL}/api/tags")

    def test_generate_url_derived_from_gpu_url(self):
        self.assertEqual(OLLAMA_GPU_GENERATE_URL, f"{OLLAMA_GPU_URL}/api/generate")

    def test_chat_url_derived_from_gpu_url(self):
        self.assertEqual(OLLAMA_GPU_CHAT_URL, f"{OLLAMA_GPU_URL}/api/chat")

    def test_nas_services_use_nas_ip(self):
        self.assertIn(NAS_IP, CHROMADB_URL)
        self.assertIn(NAS_IP, PROMETHEUS_URL)
        self.assertIn(NAS_IP, GRAFANA_URL)
        self.assertIn(NAS_IP, GLUETUN_PROXY)


class TestExistingFunctionsPreserved(unittest.TestCase):
    """Existing runtime_config functions must still work."""

    def test_load_config_returns_dict(self):
        result = load_config()
        self.assertIsInstance(result, dict)

    def test_get_telegram_settings_with_empty_config(self):
        result = get_telegram_settings({})
        self.assertEqual(result, {})

    def test_get_telegram_bot_token_from_config(self):
        cfg = {"channels": {"telegram": {"botToken": "test123"}}}
        result = get_telegram_bot_token(cfg)
        self.assertEqual(result, "test123")

    def test_get_hue_api_key_from_config(self):
        cfg = {"hue": {"apiKey": "hue-key-abc"}}
        result = get_hue_api_key(cfg)
        self.assertEqual(result, "hue-key-abc")

    def test_get_allowed_chat_ids(self):
        cfg = {"channels": {"telegram": {"allowFrom": ["123", "456"]}}}
        result = get_allowed_chat_ids(cfg)
        self.assertEqual(result, {"123", "456"})

    def test_validate_config_accepts_empty_non_strict_config(self):
        report = validate_config({}, require_secrets=False)
        self.assertTrue(report["ok"])
        self.assertEqual(report["errors"], [])

    def test_validate_config_requires_telegram_when_strict(self):
        report = validate_config({}, require_secrets=True)
        self.assertFalse(report["ok"])
        self.assertIn("telegram bot token is required", report["errors"])

    def test_validate_config_warns_on_placeholders(self):
        cfg = {
            "channels": {"telegram": {"botToken": "replace-with-telegram-bot-token"}},
            "hue": {"apiKey": "replace-with-hue-api-key"},
        }
        report = validate_config(cfg)
        self.assertTrue(report["ok"])
        self.assertIn("telegram bot token is still a placeholder", report["warnings"])
        self.assertIn("Hue API key is still a placeholder", report["warnings"])

    def test_get_config_value_walks_nested_path(self):
        cfg = {"a": {"b": {"c": 123}}}
        self.assertEqual(get_config_value(["a", "b", "c"], cfg), 123)
        self.assertEqual(get_config_value(["a", "x"], cfg, default="fallback"), "fallback")

    def test_openclaw_schema_exists(self):
        schema = Path(__file__).resolve().parents[1] / "workspace" / "runtime" / "schemas" / "openclaw_config_schema.json"
        self.assertTrue(schema.exists())


class TestPatchability(unittest.TestCase):
    """Consumers can patch constants for testing."""

    @patch("workspace.runtime.runtime_config.OLLAMA_GPU_URL", "http://test:11434")
    def test_patch_ollama_gpu_url(self):
        from workspace.runtime import runtime_config
        self.assertEqual(runtime_config.OLLAMA_GPU_URL, "http://test:11434")

    @patch("workspace.runtime.runtime_config.HUE_BRIDGE_IP", "10.0.0.99")
    def test_patch_hue_bridge_ip(self):
        from workspace.runtime import runtime_config
        self.assertEqual(runtime_config.HUE_BRIDGE_IP, "10.0.0.99")

    @patch("workspace.runtime.runtime_config.HOMELAB_IP", "10.0.0.6")
    def test_patch_homelab_ip(self):
        from workspace.runtime import runtime_config
        self.assertEqual(runtime_config.HOMELAB_IP, "10.0.0.6")


if __name__ == "__main__":
    unittest.main()

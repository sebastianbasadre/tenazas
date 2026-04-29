#!/usr/bin/env python3
"""Tests for hue_sync_box.py — Hue Sync Box integration."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
from io import BytesIO

# Add skill path — try container path first, then relative
for _p in ["/home/tenazas/.openclaw/workspace/skills/smart-home",
           os.path.join(os.path.dirname(__file__), "..", "state", "workspace", "skills", "smart-home")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import hue_sync_box as hsb


def _make_config(ip="192.168.2.36", token="fake-token-1234"):
    """Create a temp config file and return its Path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"hue_sync_box": {"ip": ip, "token": token}}, tmp)
    tmp.close()
    return Path(tmp.name)


def _mock_response(data: dict, code=200):
    """Create a mock urlopen response."""
    body = json.dumps(data).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestConfig(unittest.TestCase):
    def test_load_config_valid(self):
        cfg_path = _make_config()
        try:
            cfg = hsb._load_config(cfg_path)
            self.assertEqual(cfg["ip"], "192.168.2.36")
            self.assertEqual(cfg["token"], "fake-token-1234")
        finally:
            os.unlink(cfg_path)

    def test_load_config_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            hsb._load_config(Path("/nonexistent/config.json"))

    def test_load_config_missing_token(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"hue_sync_box": {"ip": "1.2.3.4"}}, tmp)
        tmp.close()
        try:
            with self.assertRaises(RuntimeError):
                hsb._load_config(Path(tmp.name))
        finally:
            os.unlink(tmp.name)

    def test_load_config_missing_section(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"other": "stuff"}, tmp)
        tmp.close()
        try:
            with self.assertRaises(RuntimeError):
                hsb._load_config(Path(tmp.name))
        finally:
            os.unlink(tmp.name)


class TestValidation(unittest.TestCase):
    def test_set_mode_invalid(self):
        r = hsb.set_mode("turbo")
        self.assertFalse(r["ok"])
        self.assertIn("invalido", r["error"])

    def test_sync_on_invalid_mode(self):
        r = hsb.sync_on("passthrough")
        self.assertFalse(r["ok"])
        self.assertIn("requiere", r["error"])

    def test_sync_off_invalid_mode(self):
        r = hsb.sync_off("video")
        self.assertFalse(r["ok"])
        self.assertIn("requiere", r["error"])

    def test_set_intensity_invalid(self):
        r = hsb.set_intensity("mega")
        self.assertFalse(r["ok"])
        self.assertIn("invalida", r["error"])

    def test_set_brightness_out_of_range(self):
        r = hsb.set_brightness(300)
        self.assertFalse(r["ok"])
        self.assertIn("0-200", r["error"])

    def test_set_brightness_not_int(self):
        r = hsb.set_brightness("high")
        self.assertFalse(r["ok"])

    def test_set_hdmi_invalid(self):
        r = hsb.set_hdmi_input("input5")
        self.assertFalse(r["ok"])
        self.assertIn("invalido", r["error"])


class TestAPICalls(unittest.TestCase):
    def setUp(self):
        self.cfg_path = _make_config()

    def tearDown(self):
        os.unlink(self.cfg_path)

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_get_state(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"execution": {"mode": "video"}})
        r = hsb.get_state(config_path=self.cfg_path)
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["execution"]["mode"], "video")

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_get_execution(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"mode": "passthrough", "syncActive": False})
        r = hsb.get_execution(config_path=self.cfg_path)
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["mode"], "passthrough")

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_set_mode_video(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({})
        r = hsb.set_mode("video", config_path=self.cfg_path)
        self.assertTrue(r["ok"])

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_sync_on(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({})
        r = hsb.sync_on("game", config_path=self.cfg_path)
        self.assertTrue(r["ok"])

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_sync_off(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({})
        r = hsb.sync_off("powersave", config_path=self.cfg_path)
        self.assertTrue(r["ok"])

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_set_intensity(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({})
        r = hsb.set_intensity("high", config_path=self.cfg_path)
        self.assertTrue(r["ok"])

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_set_brightness(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({})
        r = hsb.set_brightness(150, config_path=self.cfg_path)
        self.assertTrue(r["ok"])

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_set_hdmi_input(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({})
        r = hsb.set_hdmi_input("input3", config_path=self.cfg_path)
        self.assertTrue(r["ok"])

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_toggle_sync_from_active(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"mode": "video"})
        r = hsb.toggle_sync(config_path=self.cfg_path)
        self.assertTrue(r["ok"])

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_toggle_sync_from_inactive(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"mode": "passthrough"})
        r = hsb.toggle_sync(config_path=self.cfg_path)
        self.assertTrue(r["ok"])


class TestErrorHandling(unittest.TestCase):
    def setUp(self):
        self.cfg_path = _make_config()

    def tearDown(self):
        os.unlink(self.cfg_path)

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            "https://x", 401, "Unauthorized", {}, BytesIO(b""))
        r = hsb.get_state(config_path=self.cfg_path)
        self.assertFalse(r["ok"])
        self.assertIn("401", r["error"])
        self.assertEqual(r["status"], 401)

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("connection refused")
        r = hsb.get_state(config_path=self.cfg_path)
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("network"))

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_timeout(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("timed out")
        r = hsb.get_execution(config_path=self.cfg_path)
        self.assertFalse(r["ok"])
        self.assertIn("timed out", r["error"])

    def test_missing_config_graceful(self):
        r = hsb.healthcheck(config_path=Path("/nonexistent.json"))
        self.assertFalse(r["ok"])
        self.assertIn("config", r["error"])


class TestHealthcheck(unittest.TestCase):
    def setUp(self):
        self.cfg_path = _make_config()

    def tearDown(self):
        os.unlink(self.cfg_path)

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_healthy(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"name": "SyncBox"})
        r = hsb.healthcheck(config_path=self.cfg_path)
        self.assertTrue(r["ok"])
        self.assertTrue(r["reachable"])
        self.assertTrue(r["authenticated"])

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_auth_failed(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            "https://x", 401, "Unauthorized", {}, BytesIO(b""))
        r = hsb.healthcheck(config_path=self.cfg_path)
        self.assertFalse(r["ok"])
        self.assertTrue(r["reachable"])
        self.assertFalse(r["authenticated"])

    @patch("hue_sync_box.urllib.request.urlopen")
    def test_unreachable(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("refused")
        r = hsb.healthcheck(config_path=self.cfg_path)
        self.assertFalse(r["ok"])
        self.assertFalse(r["reachable"])
        self.assertFalse(r["authenticated"])

    def test_no_config(self):
        r = hsb.healthcheck(config_path=Path("/nope.json"))
        self.assertFalse(r["ok"])
        self.assertFalse(r["reachable"])


if __name__ == "__main__":
    unittest.main()

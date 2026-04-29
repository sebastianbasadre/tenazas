#!/usr/bin/env python3
"""Tests for xiaomi_vacuum.py — Xiaomi S20+ MIoT integration."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

for _p in ["/home/tenazas/.openclaw/workspace/skills/smart-home",
           os.path.join(os.path.dirname(__file__), "..", "state", "workspace", "skills", "smart-home")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import xiaomi_vacuum as vac


class TestConfig(unittest.TestCase):
    def test_load_config_no_file(self):
        with patch.object(vac, "CONFIG_PATH", Path("/nonexistent.json")):
            cfg = vac._load_config()
            self.assertEqual(cfg["ip"], vac.DEFAULT_IP)
            self.assertEqual(cfg["token"], "")

    def test_load_config_no_section(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"other": "stuff"}, tmp)
        tmp.close()
        try:
            with patch.object(vac, "CONFIG_PATH", Path(tmp.name)):
                cfg = vac._load_config()
                self.assertEqual(cfg["token"], "")
        finally:
            os.unlink(tmp.name)

    def test_load_config_valid(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"xiaomi_vacuum": {"ip": "1.2.3.4", "token": "abcd1234" * 4,
                                      "model": "test.model"}}, tmp)
        tmp.close()
        try:
            with patch.object(vac, "CONFIG_PATH", Path(tmp.name)):
                cfg = vac._load_config()
                self.assertEqual(cfg["ip"], "1.2.3.4")
                self.assertEqual(cfg["token"], "abcd1234" * 4)
                self.assertEqual(cfg["model"], "test.model")
        finally:
            os.unlink(tmp.name)


class TestValidation(unittest.TestCase):
    def test_set_mode_invalid(self):
        with patch.object(vac, "_get_device", return_value=(MagicMock(), None)):
            r = vac.set_mode("mega")
            self.assertFalse(r["ok"])
            self.assertIn("invalido", r["error"])

    def test_set_mode_valid_names(self):
        for name in ["silent", "standard", "medium", "turbo", "silencioso", "normal"]:
            self.assertIn(name, vac.MODE_MAP)

    def test_start_clean_invalid_mode(self):
        dev = MagicMock()
        with patch.object(vac, "_get_device", return_value=(dev, None)):
            r = vac.start_clean(mode="mega")
            self.assertFalse(r["ok"])


class TestHealthcheck(unittest.TestCase):
    @patch("socket.socket")
    def test_healthcheck_no_token(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        header = b"\x21\x31\x00\x20\x00\x00\x00\x00"
        header += b"\x44\xd7\x0f\xb6"
        header += b"\x00\x00\xd7\x49"
        header += b"\xff" * 16
        mock_sock.recvfrom.return_value = (header, ("192.168.2.99", 54321))
        with patch.object(vac, "_load_config", return_value={"ip": "192.168.2.99", "token": "", "model": ""}):
            r = vac.healthcheck()
            self.assertTrue(r["ok"])
            self.assertTrue(r["reachable"])
            self.assertFalse(r["authenticated"])

    @patch("socket.socket")
    def test_healthcheck_timeout(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        import socket as real_socket
        mock_sock.recvfrom.side_effect = real_socket.timeout("timed out")
        with patch.object(vac, "_load_config", return_value={"ip": "192.168.2.99", "token": "", "model": ""}):
            r = vac.healthcheck()
            self.assertFalse(r["ok"])
            self.assertFalse(r["reachable"])

    def test_healthcheck_with_token(self):
        mock_dev = MagicMock()
        mock_info = MagicMock()
        mock_info.model = "xiaomi.vacuum.b108gl"
        mock_info.firmware_version = "1.0.0"
        mock_info.mac_address = "AA:BB:CC:DD:EE:FF"
        mock_dev.info.return_value = mock_info
        with patch.object(vac, "_get_device", return_value=(mock_dev, None)):
            with patch.object(vac, "_load_config", return_value={"ip": "1.2.3.4", "token": "x" * 32, "model": ""}):
                r = vac.healthcheck()
                self.assertTrue(r["ok"])
                self.assertTrue(r["authenticated"])


class TestCommands(unittest.TestCase):
    def test_get_status_no_token(self):
        with patch.object(vac, "_load_config", return_value={"ip": "1.2.3.4", "token": "", "model": ""}):
            r = vac.get_status()
            self.assertFalse(r["ok"])
            self.assertTrue(r.get("needs_token"))

    def test_get_status_with_device(self):
        mock_dev = MagicMock()
        mock_dev.get_properties.return_value = [
            {"did": "status", "value": 5},
            {"did": "battery", "value": 85},
            {"did": "mode", "value": 1},
        ]
        with patch.object(vac, "_get_device", return_value=(mock_dev, None)):
            r = vac.get_status()
            self.assertTrue(r["ok"])
            self.assertEqual(r["result"]["status"], "charging")
            self.assertEqual(r["result"]["battery"], 85)

    def test_start_clean(self):
        mock_dev = MagicMock()
        with patch.object(vac, "_get_device", return_value=(mock_dev, None)):
            r = vac.start_clean()
            self.assertTrue(r["ok"])
            mock_dev.call_action.assert_called_once_with(
                vac.SIID_VACUUM, vac.AIID_START)

    def test_start_clean_with_mode(self):
        mock_dev = MagicMock()
        with patch.object(vac, "_get_device", return_value=(mock_dev, None)):
            r = vac.start_clean(mode="turbo")
            self.assertTrue(r["ok"])
            mock_dev.set_property.assert_called_once_with(
                vac.SIID_VACUUM, vac.PIID_MODE, 3)

    def test_stop_clean(self):
        mock_dev = MagicMock()
        with patch.object(vac, "_get_device", return_value=(mock_dev, None)):
            r = vac.stop_clean()
            self.assertTrue(r["ok"])

    def test_go_home(self):
        mock_dev = MagicMock()
        with patch.object(vac, "_get_device", return_value=(mock_dev, None)):
            r = vac.go_home()
            self.assertTrue(r["ok"])
            mock_dev.call_action.assert_called_once_with(
                vac.SIID_CHARGE, vac.AIID_CHARGE)

    def test_get_battery(self):
        mock_dev = MagicMock()
        mock_dev.get_properties.return_value = [
            {"did": "battery", "value": 72},
        ]
        with patch.object(vac, "_get_device", return_value=(mock_dev, None)):
            r = vac.get_battery()
            self.assertTrue(r["ok"])
            self.assertEqual(r["battery"], 72)

    def test_set_mode_valid(self):
        mock_dev = MagicMock()
        with patch.object(vac, "_get_device", return_value=(mock_dev, None)):
            r = vac.set_mode("turbo")
            self.assertTrue(r["ok"])
            mock_dev.set_property.assert_called_once_with(
                vac.SIID_VACUUM, vac.PIID_MODE, 3)


if __name__ == "__main__":
    unittest.main()

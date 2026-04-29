#!/usr/bin/env python3
"""Tests for marantz_avr.py, google_cast.py, samsung_tv.py, xiaomi_vacuum.py"""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
from io import BytesIO

for _p in ["/home/tenazas/.openclaw/workspace/skills/smart-home",
           os.path.join(os.path.dirname(__file__), "..", "state", "workspace", "skills", "smart-home")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import marantz_avr as avr
import google_cast as cast
import samsung_tv as tv
import xiaomi_vacuum as vac


def _mock_response(data, code=200):
    resp = MagicMock()
    if isinstance(data, str):
        resp.read.return_value = data.encode("utf-8")
    else:
        resp.read.return_value = json.dumps(data).encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


MARANTZ_XML = """<?xml version="1.0" encoding="utf-8" ?>
<item>
<Power><value>ON</value></Power>
<InputFuncSelect><value>MPLAY</value></InputFuncSelect>
<VolumeDisplay><value>Absolute</value></VolumeDisplay>
<MasterVolume><value>-35.0</value></MasterVolume>
<Mute><value>off</value></Mute>
</item>"""


class TestMarantzValidation(unittest.TestCase):
    def test_set_input_invalid(self):
        r = avr.set_input("xbox")
        self.assertFalse(r["ok"])

    def test_set_surround_invalid(self):
        r = avr.set_surround("mega")
        self.assertFalse(r["ok"])

    def test_set_volume_out_of_range(self):
        r = avr.set_volume(200)
        self.assertFalse(r["ok"])

    def test_set_volume_not_int(self):
        r = avr.set_volume("loud")
        self.assertFalse(r["ok"])


class TestMarantzAPI(unittest.TestCase):
    @patch("marantz_avr.urllib.request.urlopen")
    def test_get_status(self, mock):
        mock.return_value = _mock_response(MARANTZ_XML)
        r = avr.get_status()
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["Power"], "ON")
        self.assertEqual(r["result"]["MasterVolume"], "-35.0")

    @patch("marantz_avr.urllib.request.urlopen")
    def test_power_on(self, mock):
        mock.return_value = _mock_response("")
        r = avr.power_on()
        self.assertTrue(r["ok"])

    @patch("marantz_avr.urllib.request.urlopen")
    def test_set_input_valid(self, mock):
        mock.return_value = _mock_response("")
        r = avr.set_input("bluetooth")
        self.assertTrue(r["ok"])

    @patch("marantz_avr.urllib.request.urlopen")
    def test_set_surround_valid(self, mock):
        mock.return_value = _mock_response("")
        r = avr.set_surround("movie")
        self.assertTrue(r["ok"])

    @patch("marantz_avr.urllib.request.urlopen")
    def test_healthcheck_online(self, mock):
        mock.return_value = _mock_response(MARANTZ_XML)
        r = avr.healthcheck()
        self.assertTrue(r["ok"])
        self.assertTrue(r["reachable"])

    @patch("marantz_avr.urllib.request.urlopen")
    def test_healthcheck_offline(self, mock):
        mock.side_effect = URLError("refused")
        r = avr.healthcheck()
        self.assertFalse(r["ok"])
        self.assertFalse(r["reachable"])

    @patch("marantz_avr.urllib.request.urlopen")
    def test_mute_toggle_from_off(self, mock):
        mock.return_value = _mock_response(MARANTZ_XML)  # Mute=off
        r = avr.mute_toggle()
        self.assertTrue(r["ok"])


class TestGoogleCast(unittest.TestCase):
    def test_resolve_unknown_device(self):
        r = cast.get_info("bathroom")
        self.assertFalse(r["ok"])

    @patch("google_cast.urllib.request.urlopen")
    def test_get_info(self, mock):
        mock.return_value = _mock_response({"name": "Test Speaker", "build_version": "123",
                                             "locale": "es", "connected": True, "uptime": 7200, "ssid": "WiFi"})
        r = cast.get_info("living")
        self.assertTrue(r["ok"])
        self.assertEqual(r["name"], "Test Speaker")
        self.assertEqual(r["uptime_hours"], 2.0)

    @patch("google_cast.urllib.request.urlopen")
    def test_set_volume_valid(self, mock):
        mock.return_value = _mock_response({})
        r = cast.set_volume(0.5, "living")
        self.assertTrue(r["ok"])

    def test_set_volume_out_of_range(self):
        r = cast.set_volume(1.5, "living")
        self.assertFalse(r["ok"])

    def test_set_volume_pct_out_of_range(self):
        r = cast.set_volume_pct(150, "living")
        self.assertFalse(r["ok"])

    @patch("google_cast.urllib.request.urlopen")
    def test_healthcheck_online(self, mock):
        mock.return_value = _mock_response({"name": "Speaker"})
        r = cast.healthcheck("living")
        self.assertTrue(r["ok"])

    @patch("google_cast.urllib.request.urlopen")
    def test_healthcheck_offline(self, mock):
        mock.side_effect = URLError("timeout")
        r = cast.healthcheck("living")
        self.assertFalse(r["ok"])

    def test_list_devices(self):
        devices = cast.list_devices()
        self.assertGreater(len(devices), 0)
        self.assertIn("ip", devices[0])


class TestSamsungTV(unittest.TestCase):
    @patch("samsung_tv.urllib.request.urlopen")
    def test_get_info_online(self, mock):
        mock.return_value = _mock_response({"device": {"name": "Samsung Q70", "modelName": "QN85",
                                                        "ip": "192.168.2.29", "wifiMac": "aa:bb", "OS": "Tizen"}})
        r = tv.get_info()
        self.assertTrue(r["ok"])
        self.assertEqual(r["name"], "Samsung Q70")
        self.assertEqual(r["power"], "on")

    @patch("samsung_tv.urllib.request.urlopen")
    def test_get_info_offline(self, mock):
        mock.side_effect = URLError("refused")
        r = tv.get_info()
        self.assertFalse(r["ok"])
        self.assertEqual(r["power"], "off/unreachable")

    def test_wake_on_lan(self):
        r = tv.wake_on_lan()
        self.assertTrue(r["ok"])
        self.assertEqual(r["action"], "WOL sent")

    @patch("samsung_tv.urllib.request.urlopen")
    def test_healthcheck_on(self, mock):
        mock.return_value = _mock_response({"device": {"name": "TV"}})
        r = tv.healthcheck()
        self.assertTrue(r["ok"])
        self.assertEqual(r["power"], "on")

    @patch("samsung_tv.urllib.request.urlopen")
    def test_healthcheck_off(self, mock):
        mock.side_effect = URLError("timeout")
        r = tv.healthcheck()
        self.assertFalse(r["ok"])
        self.assertEqual(r["power"], "off")


class TestXiaomiVacuum(unittest.TestCase):
    @patch("xiaomi_vacuum.socket.socket")
    def test_healthcheck_online(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        # Build a valid miio response (32 bytes header)
        header = b"\x21\x31\x00\x20\x00\x00\x00\x00"
        header += b"\x44\xd7\x0f\xb6"  # device_id
        header += b"\x00\x00\xd7\x49"  # stamp
        header += b"\xff" * 16  # checksum
        mock_sock.recvfrom.return_value = (header, ("192.168.2.99", 54321))
        r = vac.healthcheck()
        self.assertTrue(r["ok"])
        self.assertTrue(r["reachable"])
        self.assertEqual(r["device_id"], 1154944950)

    @patch("xiaomi_vacuum.socket.socket")
    def test_healthcheck_timeout(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_socket_cls.return_value = mock_sock
        import socket as real_socket
        mock_sock.recvfrom.side_effect = real_socket.timeout("timed out")
        r = vac.healthcheck()
        self.assertFalse(r["ok"])
        self.assertFalse(r["reachable"])

    def test_get_status_no_token(self):
        r = vac.get_status()
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("needs_token") or r.get("needs_crypto"))


if __name__ == "__main__":
    unittest.main()

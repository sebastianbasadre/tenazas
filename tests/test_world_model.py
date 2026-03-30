#!/usr/bin/env python3
"""
Tests for world_model.py — Infrastructure state representation.

Covers:
- get_entity() for all entity types (container, device, service, model, skill)
- get_entity() with specific name lookups
- get_entity() unknown type handling
- get_status() complete snapshot
- get_topology() network representation
- State loading (missing file, valid state, corrupt JSON)
- Known devices/services/models data integrity

Corre: python3 tests/test_world_model.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import world_model as wm

# Sample state for mocking
SAMPLE_STATE = {
    "containers": [
        {"name": "gluetun", "up": True},
        {"name": "sonarr", "up": True},
        {"name": "radarr", "up": False},
        {"name": "qbittorrent", "up": True},
    ],
    "internet": {"reachable": True, "latency_ms": 25},
    "vpn": {"connected": True, "country": "ES"},
    "ollama_notebook": {"status": "ok"},
    "ollama_nas": {"status": "offline"},
    "network_devices": {"count": 12},
}


def _mock_state(state=None):
    """Patch _load_state to return given state."""
    return patch("runtime.world_model._load_state", return_value=state or SAMPLE_STATE)


class TestGetEntityContainer(unittest.TestCase):
    """get_entity('container', ...) from state.json."""

    @_mock_state()
    def test_list_all_containers(self, mock):
        result = wm.get_entity("container")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 4)

    @_mock_state()
    def test_get_specific_container(self, mock):
        result = wm.get_entity("container", "sonarr")
        self.assertEqual(result["name"], "sonarr")
        self.assertTrue(result["up"])

    @_mock_state()
    def test_get_missing_container(self, mock):
        result = wm.get_entity("container", "nginx")
        self.assertEqual(result, {})

    def test_empty_state_returns_empty_list(self):
        with patch("runtime.world_model._load_state", return_value={}):
            result = wm.get_entity("container")
            self.assertEqual(result, [])


class TestGetEntityDevice(unittest.TestCase):
    """get_entity('device', ...) from KNOWN_DEVICES."""

    @_mock_state()
    def test_list_all_devices(self, mock):
        result = wm.get_entity("device")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), len(wm.KNOWN_DEVICES))

    @_mock_state()
    def test_get_device_by_ip(self, mock):
        result = wm.get_entity("device", "192.168.2.2")
        self.assertEqual(result["name"], "NAS DS923+")
        self.assertEqual(result["ip"], "192.168.2.2")

    @_mock_state()
    def test_get_device_by_name(self, mock):
        result = wm.get_entity("device", "Hue Bridge")
        self.assertEqual(result["ip"], "192.168.2.11")

    @_mock_state()
    def test_get_device_by_name_case_insensitive(self, mock):
        result = wm.get_entity("device", "msi notebook")
        self.assertEqual(result["ip"], "192.168.2.76")

    @_mock_state()
    def test_get_unknown_device(self, mock):
        result = wm.get_entity("device", "192.168.2.99")
        self.assertEqual(result, {})

    @_mock_state()
    def test_device_has_type(self, mock):
        result = wm.get_entity("device", "192.168.2.11")
        self.assertEqual(result["type"], "smart_home")


class TestGetEntityService(unittest.TestCase):
    """get_entity('service', ...) from KNOWN_SERVICES."""

    @_mock_state()
    def test_list_all_services(self, mock):
        result = wm.get_entity("service")
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result), len(wm.KNOWN_SERVICES))

    @_mock_state()
    def test_get_specific_service(self, mock):
        result = wm.get_entity("service", "chromadb")
        self.assertEqual(result["type"], "vector_db")
        self.assertIn("8100", result["url"])

    @_mock_state()
    def test_get_unknown_service(self, mock):
        result = wm.get_entity("service", "redis")
        self.assertEqual(result, {})


class TestGetEntityModel(unittest.TestCase):
    """get_entity('model', ...) from KNOWN_MODELS."""

    @_mock_state()
    def test_list_all_models(self, mock):
        result = wm.get_entity("model")
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result), len(wm.KNOWN_MODELS))

    @_mock_state()
    def test_get_specific_model(self, mock):
        result = wm.get_entity("model", "qwen2.5:7b")
        self.assertEqual(result["params"], "7B")

    @_mock_state()
    def test_get_unknown_model(self, mock):
        result = wm.get_entity("model", "llama3:70b")
        self.assertEqual(result, {})


class TestGetEntitySkill(unittest.TestCase):
    """get_entity('skill') returns count."""

    @_mock_state()
    def test_skill_returns_count(self, mock):
        result = wm.get_entity("skill")
        self.assertIn("count", result)
        self.assertEqual(result["count"], 22)


class TestGetEntityUnknown(unittest.TestCase):
    """Unknown entity types return empty and log error."""

    @_mock_state()
    @patch("runtime.world_model._log")
    def test_unknown_type_returns_empty(self, mock_log, mock_state):
        result = wm.get_entity("spaceship")
        self.assertEqual(result, {})

    @_mock_state()
    @patch("runtime.world_model._log")
    def test_unknown_type_logs_error(self, mock_log, mock_state):
        wm.get_entity("spaceship")
        mock_log.assert_called_once()
        entry = mock_log.call_args[0][0]
        self.assertEqual(entry["error"], "unknown entity type")


class TestGetStatus(unittest.TestCase):
    """get_status() complete snapshot."""

    @_mock_state()
    @patch("runtime.world_model._log")
    def test_status_has_required_keys(self, mock_log, mock_state):
        status = wm.get_status()
        for key in ["ts", "containers", "internet", "vpn",
                     "ollama_notebook", "known_devices", "known_services",
                     "known_models", "skills"]:
            self.assertIn(key, status)

    @_mock_state()
    @patch("runtime.world_model._log")
    def test_container_counts(self, mock_log, mock_state):
        status = wm.get_status()
        self.assertEqual(status["containers"]["total"], 4)
        self.assertEqual(status["containers"]["up"], 3)
        self.assertEqual(status["containers"]["down"], 1)

    @_mock_state()
    @patch("runtime.world_model._log")
    def test_down_names_listed(self, mock_log, mock_state):
        status = wm.get_status()
        self.assertIn("radarr", status["containers"]["down_names"])

    @_mock_state()
    @patch("runtime.world_model._log")
    def test_internet_from_state(self, mock_log, mock_state):
        status = wm.get_status()
        self.assertTrue(status["internet"]["reachable"])

    @patch("runtime.world_model._log")
    def test_empty_state_safe(self, mock_log):
        with patch("runtime.world_model._load_state", return_value={}):
            status = wm.get_status()
            self.assertEqual(status["containers"]["total"], 0)
            self.assertEqual(status["containers"]["up"], 0)

    @_mock_state()
    @patch("runtime.world_model._log")
    def test_status_logs(self, mock_log, mock_state):
        wm.get_status()
        mock_log.assert_called_once()


class TestGetTopology(unittest.TestCase):
    """get_topology() network representation."""

    @patch("runtime.world_model._log")
    def test_topology_has_subnet(self, mock_log):
        topo = wm.get_topology()
        self.assertEqual(topo["subnet"], "192.168.2.0/24")

    @patch("runtime.world_model._log")
    def test_topology_has_gateway(self, mock_log):
        topo = wm.get_topology()
        self.assertEqual(topo["gateway"], "192.168.2.1")

    @patch("runtime.world_model._log")
    def test_topology_has_devices(self, mock_log):
        topo = wm.get_topology()
        self.assertIn("192.168.2.2", topo["devices"])

    @patch("runtime.world_model._log")
    def test_topology_services_have_url_and_host(self, mock_log):
        topo = wm.get_topology()
        for name, svc in topo["services"].items():
            self.assertIn("url", svc)
            self.assertIn("host", svc)

    @patch("runtime.world_model._log")
    def test_topology_logs(self, mock_log):
        wm.get_topology()
        mock_log.assert_called_once()


class TestLoadState(unittest.TestCase):
    """State loading handles missing/corrupt files."""

    def test_missing_file_returns_empty(self):
        with patch("runtime.world_model.STATE_FILE", Path("/nonexistent/state.json")):
            result = wm._load_state()
            self.assertEqual(result, {})

    def test_corrupt_json_returns_empty(self):
        tmpdir = tempfile.mkdtemp()
        bad_file = Path(tmpdir) / "state.json"
        bad_file.write_text("{broken json")
        with patch("runtime.world_model.STATE_FILE", bad_file):
            result = wm._load_state()
            self.assertEqual(result, {})

    def test_valid_json_loaded(self):
        tmpdir = tempfile.mkdtemp()
        good_file = Path(tmpdir) / "state.json"
        good_file.write_text(json.dumps({"containers": []}))
        with patch("runtime.world_model.STATE_FILE", good_file):
            result = wm._load_state()
            self.assertIn("containers", result)


class TestDataIntegrity(unittest.TestCase):
    """Known devices, services, models have consistent data."""

    def test_all_devices_have_name_and_type(self):
        for ip, info in wm.KNOWN_DEVICES.items():
            self.assertIn("name", info, f"{ip} missing name")
            self.assertIn("type", info, f"{ip} missing type")

    def test_all_services_have_required_fields(self):
        for name, svc in wm.KNOWN_SERVICES.items():
            self.assertIn("url", svc, f"{name} missing url")
            self.assertIn("type", svc, f"{name} missing type")
            self.assertIn("host", svc, f"{name} missing host")

    def test_all_models_have_required_fields(self):
        for name, info in wm.KNOWN_MODELS.items():
            self.assertIn("params", info, f"{name} missing params")
            self.assertIn("use", info, f"{name} missing use")
            self.assertIn("backend", info, f"{name} missing backend")

    def test_nas_ip_in_devices(self):
        self.assertIn("192.168.2.2", wm.KNOWN_DEVICES)

    def test_notebook_ip_in_devices(self):
        self.assertIn("192.168.2.76", wm.KNOWN_DEVICES)


if __name__ == "__main__":
    unittest.main()

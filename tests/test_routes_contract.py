"""
test_routes_contract.py — Tests del contrato de routing.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workspace", "runtime"))
sys.path.insert(0, "/home/tenazas/runtime")

import unittest
from routes_contract import (
    VALID_ROUTES,
    DEPRECATED_ALIASES,
    is_valid_route,
    normalize_route,
    assert_valid_route,
)


class TestValidRoutes(unittest.TestCase):

    def test_all_official_labels_are_valid(self):
        for route in VALID_ROUTES:
            self.assertTrue(is_valid_route(route), f"'{route}' debería ser válido")

    def test_none_is_always_valid(self):
        self.assertTrue(is_valid_route("none"))

    def test_unknown_route_is_invalid(self):
        self.assertFalse(is_valid_route("unknown_route"))
        self.assertFalse(is_valid_route(""))
        self.assertFalse(is_valid_route("network-scanner"))


class TestNormalizeRoute(unittest.TestCase):

    def test_valid_route_passthrough(self):
        for route in VALID_ROUTES:
            self.assertEqual(normalize_route(route), route)

    def test_deprecated_alias_network_scanner(self):
        self.assertEqual(normalize_route("network-scanner"), "network_scan")
        self.assertEqual(normalize_route("network_scanner"), "network_scan")

    def test_deprecated_alias_smart_home(self):
        self.assertEqual(normalize_route("smart-home-on"), "lights")
        self.assertEqual(normalize_route("smart-home-off"), "lights")

    def test_deprecated_alias_infra_monitor(self):
        self.assertEqual(normalize_route("infra-monitor"), "infra")
        self.assertEqual(normalize_route("infra_monitor"), "infra")

    def test_deprecated_alias_arr_monitor(self):
        self.assertEqual(normalize_route("arr-monitor"), "arr_monitor")

    def test_unknown_route_returns_none(self):
        self.assertEqual(normalize_route("garbage"), "none")
        self.assertEqual(normalize_route(""), "none")
        self.assertEqual(normalize_route("   "), "none")

    def test_none_input_returns_none(self):
        self.assertEqual(normalize_route(None), "none")

    def test_whitespace_stripped(self):
        self.assertEqual(normalize_route("  weather  "), "weather")

    def test_uppercase_normalized(self):
        self.assertEqual(normalize_route("WEATHER"), "weather")
        self.assertEqual(normalize_route("Network_Scan"), "network_scan")


class TestAssertValidRoute(unittest.TestCase):

    def test_valid_route_passes(self):
        for route in VALID_ROUTES:
            result = assert_valid_route(route)
            self.assertEqual(result, route)

    def test_deprecated_alias_normalized(self):
        self.assertEqual(assert_valid_route("network-scanner"), "network_scan")

    def test_unknown_route_raises(self):
        with self.assertRaises(ValueError):
            assert_valid_route("garbage_route")


class TestContractCompleteness(unittest.TestCase):

    def test_all_deprecated_aliases_point_to_valid_routes(self):
        for alias, target in DEPRECATED_ALIASES.items():
            self.assertIn(
                target, VALID_ROUTES,
                f"Alias '{alias}' apunta a '{target}' que no está en VALID_ROUTES",
            )

    def test_network_scan_is_canonical(self):
        self.assertIn("network_scan", VALID_ROUTES)
        self.assertNotIn("network-scanner", VALID_ROUTES)
        self.assertNotIn("network_scanner", VALID_ROUTES)

    def test_none_is_in_valid_routes(self):
        self.assertIn("none", VALID_ROUTES)

    def test_no_hyphens_in_valid_routes(self):
        for route in VALID_ROUTES:
            self.assertNotIn(
                "-", route,
                f"Route '{route}' tiene guión — usar underscore",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

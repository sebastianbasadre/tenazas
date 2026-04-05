#!/usr/bin/env python3
"""
Tests for iol_broker skill — InvertirOnline integration.

50 tests covering: IOLClient auth/refresh, portfolio, orders, handler, orchestrator, init_token.
All mocked — no real API calls.

Corre: python3 tests/test_iol_broker.py
"""

import sys
import json
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

sys.path.insert(0, "/home/tenazas/.openclaw/workspace/skills")
sys.path.insert(0, "/home/tenazas")

# ── IOLClient Tests ─────────────────────────────────────────────


class TestIOLClientToken(unittest.TestCase):
    """IOL Client token validation and refresh — 12 tests."""

    def _make_client(self, token="tok", refresh="ref", expires_at=None):
        with patch("iol_broker.iol_client._load_config", return_value={
            "bearer_token": token,
            "refresh_token": refresh,
            "token_expires_at": expires_at,
            "base_url": "https://api.invertironline.com",
        }):
            from iol_broker.iol_client import IOLClient
            return IOLClient()

    def test_token_valid_when_future_expiry(self):
        c = self._make_client(expires_at=time.time() + 300)
        self.assertTrue(c._token_valid())

    def test_token_invalid_when_expired(self):
        c = self._make_client(expires_at=time.time() - 100)
        self.assertFalse(c._token_valid())

    def test_token_invalid_when_none(self):
        c = self._make_client(expires_at=None)
        self.assertFalse(c._token_valid())

    def test_token_invalid_within_margin_60s(self):
        c = self._make_client(expires_at=time.time() + 30)  # < 60s margin
        self.assertFalse(c._token_valid())

    @patch("iol_broker.iol_client._save_config")
    @patch("iol_broker.iol_client.urlopen")
    @patch("iol_broker.iol_client._log")
    def test_refresh_sends_urlencoded_not_json(self, mock_log, mock_urlopen, mock_save):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "new_at", "refresh_token": "new_rt",
            "expires_in": 899, "token_type": "bearer",
        }).encode()
        mock_urlopen.return_value = mock_resp

        c = self._make_client(refresh="old_rt")
        c._refresh()

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Content-type"), "application/x-www-form-urlencoded")

    @patch("iol_broker.iol_client._save_config")
    @patch("iol_broker.iol_client.urlopen")
    @patch("iol_broker.iol_client._log")
    def test_refresh_posts_to_slash_token(self, mock_log, mock_urlopen, mock_save):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "at", "refresh_token": "rt", "expires_in": 899,
        }).encode()
        mock_urlopen.return_value = mock_resp

        c = self._make_client()
        c._refresh()

        req = mock_urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/token"))
        self.assertNotIn("/api/v2/token", req.full_url)

    @patch("iol_broker.iol_client._save_config")
    @patch("iol_broker.iol_client.urlopen")
    @patch("iol_broker.iol_client._log")
    def test_refresh_updates_bearer_token(self, mock_log, mock_urlopen, mock_save):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "NEWBEARER", "refresh_token": "rt", "expires_in": 899,
        }).encode()
        mock_urlopen.return_value = mock_resp

        c = self._make_client()
        c._refresh()
        self.assertEqual(c.bearer_token, "NEWBEARER")

    @patch("iol_broker.iol_client._save_config")
    @patch("iol_broker.iol_client.urlopen")
    @patch("iol_broker.iol_client._log")
    def test_refresh_updates_refresh_token(self, mock_log, mock_urlopen, mock_save):
        """The new refresh_token from response must be persisted."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "at", "refresh_token": "NEWREFRESH", "expires_in": 899,
        }).encode()
        mock_urlopen.return_value = mock_resp

        c = self._make_client(refresh="OLDREFRESH")
        c._refresh()
        self.assertEqual(c.refresh_token, "NEWREFRESH")
        # Verify it was saved
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved["refresh_token"], "NEWREFRESH")

    @patch("iol_broker.iol_client._save_config")
    @patch("iol_broker.iol_client.urlopen")
    @patch("iol_broker.iol_client._log")
    def test_refresh_updates_expiry_with_expires_in(self, mock_log, mock_urlopen, mock_save):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "at", "refresh_token": "rt", "expires_in": 899,
        }).encode()
        mock_urlopen.return_value = mock_resp

        before = time.time()
        c = self._make_client()
        c._refresh()
        # token_expires_at should be approximately now + 899 - 10
        self.assertAlmostEqual(c.token_expires_at, before + 889, delta=5)

    @patch("iol_broker.iol_client._save_config")
    @patch("iol_broker.iol_client.urlopen")
    @patch("iol_broker.iol_client._log")
    def test_get_calls_refresh_when_expired(self, mock_log, mock_urlopen, mock_save):
        # First call: refresh response
        refresh_resp = MagicMock()
        refresh_resp.read.return_value = json.dumps({
            "access_token": "fresh", "refresh_token": "rt", "expires_in": 899,
        }).encode()
        # Second call: actual GET response
        get_resp = MagicMock()
        get_resp.read.return_value = json.dumps({"data": "ok"}).encode()

        mock_urlopen.side_effect = [refresh_resp, get_resp]

        c = self._make_client(expires_at=time.time() - 100)  # expired
        result = c.get("/api/v2/test")
        self.assertEqual(result["data"], "ok")
        self.assertEqual(mock_urlopen.call_count, 2)  # refresh + get

    @patch("iol_broker.iol_client.urlopen")
    @patch("iol_broker.iol_client._log")
    def test_get_includes_auth_header(self, mock_log, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({}).encode()
        mock_urlopen.return_value = mock_resp

        c = self._make_client(token="MYTOKEN", expires_at=time.time() + 300)
        c.get("/api/v2/test")

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer MYTOKEN")

    @patch("iol_broker.iol_client.urlopen")
    @patch("iol_broker.iol_client._log")
    def test_log_written_on_request(self, mock_log, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({}).encode()
        mock_urlopen.return_value = mock_resp

        c = self._make_client(expires_at=time.time() + 300)
        c.get("/api/v2/test")
        self.assertTrue(mock_log.called)


# ── Portfolio Tests ─────────────────────────────────────────────


class TestIOLPortfolio(unittest.TestCase):
    """IOL Portfolio queries and formatting — 10 tests."""

    @patch("iol_broker.iol_portfolio.IOLClient")
    def test_get_portfolio_default_pais(self, MockClient):
        instance = MockClient.return_value
        instance.get.return_value = {}
        from iol_broker.iol_portfolio import get_portfolio
        get_portfolio()
        instance.get.assert_called_with("/api/v2/portafolio/argentina")

    @patch("iol_broker.iol_portfolio.IOLClient")
    def test_get_portfolio_custom_pais(self, MockClient):
        instance = MockClient.return_value
        instance.get.return_value = {}
        from iol_broker.iol_portfolio import get_portfolio
        get_portfolio("estados_unidos")
        instance.get.assert_called_with("/api/v2/portafolio/estados_unidos")

    @patch("iol_broker.iol_portfolio.IOLClient")
    def test_get_portfolio_returns_json(self, MockClient):
        instance = MockClient.return_value
        instance.get.return_value = {"activos": []}
        from iol_broker.iol_portfolio import get_portfolio
        result = get_portfolio()
        self.assertIsInstance(result, dict)

    @patch("iol_broker.iol_portfolio.IOLClient")
    def test_get_portfolio_raises_on_http_error(self, MockClient):
        from urllib.error import HTTPError
        instance = MockClient.return_value
        instance.get.side_effect = HTTPError("/test", 500, "error", {}, None)
        from iol_broker.iol_portfolio import get_portfolio
        with self.assertRaises(HTTPError):
            get_portfolio()

    @patch("iol_broker.iol_portfolio.IOLClient")
    def test_get_account_state_endpoint(self, MockClient):
        instance = MockClient.return_value
        instance.get.return_value = {}
        from iol_broker.iol_portfolio import get_account_state
        get_account_state()
        instance.get.assert_called_with("/api/v2/estadocuenta")

    @patch("iol_broker.iol_portfolio.IOLClient")
    def test_get_cotizacion_endpoint(self, MockClient):
        instance = MockClient.return_value
        instance.get.return_value = {}
        from iol_broker.iol_portfolio import get_cotizacion
        get_cotizacion("bCBA", "GGAL")
        instance.get.assert_called_with("/api/v2/bCBA/Titulos/GGAL/Cotizacion")

    @patch("iol_broker.iol_portfolio.IOLClient")
    def test_get_operations_endpoint(self, MockClient):
        instance = MockClient.return_value
        instance.get.return_value = {}
        from iol_broker.iol_portfolio import get_operations
        get_operations()
        instance.get.assert_called_with("/api/v2/operaciones")

    def test_format_portfolio_telegram_contains_total(self):
        from iol_broker.iol_portfolio import format_portfolio_telegram
        # Real IOL response shape: {"activos": [{valorizado, ...}]}
        data = {"activos": [
            {"valorizado": 5000, "titulo": {"simbolo": "GGAL", "moneda": "peso_Argentino"},
             "cantidad": 100, "ppc": 50, "gananciaPorcentaje": 2.0, "gananciaDinero": 100},
        ]}
        acct = {"cuentas": [{"disponible": 100000, "titulosValorizados": 5000, "total": 105000}],
                "totalEnPesos": 105000}
        text = format_portfolio_telegram(data, acct)
        self.assertIn("Total", text)
        self.assertIn("105,000", text)

    def test_format_portfolio_telegram_contains_posiciones(self):
        from iol_broker.iol_portfolio import format_portfolio_telegram
        data = {"activos": [
            {"titulo": {"simbolo": "GGAL", "descripcion": "Grupo Galicia", "moneda": "peso_Argentino"},
             "cantidad": 100, "ppc": 1500, "valorizado": 150000,
             "gananciaPorcentaje": 5.2, "gananciaDinero": 7800},
        ]}
        text = format_portfolio_telegram(data)
        self.assertIn("GGAL", text)
        self.assertIn("150,000", text)
        self.assertIn("5.2%", text)

    def test_format_cotizacion_telegram_contains_simbolo(self):
        from iol_broker.iol_portfolio import format_cotizacion_telegram
        data = {"ultimoPrecio": 1523, "apertura": 1480, "cierreAnterior": 1490,
                "maximo": 1530, "minimo": 1475, "volumen": 1234567, "mercado": "Merval"}
        text = format_cotizacion_telegram("GGAL", data)
        self.assertIn("GGAL", text)
        self.assertIn("1,523", text)


# ── Orders Tests ────────────────────────────────────────────────


class TestIOLOrders(unittest.TestCase):
    """IOL Orders — 12 tests."""

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_comprar_endpoint(self, mock_log, MockClient):
        instance = MockClient.return_value
        instance.post.return_value = {"numero": 123}
        from iol_broker.iol_orders import comprar
        comprar("GGAL", 100, 1500)
        instance.post.assert_called_once()
        path = instance.post.call_args[0][0]
        self.assertEqual(path, "/api/v2/operar/Comprar")

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_comprar_payload_has_required_fields(self, mock_log, MockClient):
        instance = MockClient.return_value
        instance.post.return_value = {}
        from iol_broker.iol_orders import comprar
        comprar("GGAL", 100, 1500)
        payload = instance.post.call_args[0][1]
        for field in ("mercado", "simbolo", "cantidad", "precio", "plazo",
                       "tipoOrden", "validez"):
            self.assertIn(field, payload, f"Missing: {field}")

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_comprar_default_mercado_bcba(self, mock_log, MockClient):
        instance = MockClient.return_value
        instance.post.return_value = {}
        from iol_broker.iol_orders import comprar
        comprar("GGAL", 100, 1500)
        payload = instance.post.call_args[0][1]
        self.assertEqual(payload["mercado"], "bCBA")

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_comprar_default_plazo_t0(self, mock_log, MockClient):
        instance = MockClient.return_value
        instance.post.return_value = {}
        from iol_broker.iol_orders import comprar
        comprar("GGAL", 100, 1500)
        payload = instance.post.call_args[0][1]
        self.assertEqual(payload["plazo"], "t0")

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_comprar_tipo_orden_es_precio_limite(self, mock_log, MockClient):
        instance = MockClient.return_value
        instance.post.return_value = {}
        from iol_broker.iol_orders import comprar
        comprar("GGAL", 100, 1500)
        payload = instance.post.call_args[0][1]
        self.assertEqual(payload["tipoOrden"], "precioLimite")

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_vender_endpoint(self, mock_log, MockClient):
        instance = MockClient.return_value
        instance.post.return_value = {}
        from iol_broker.iol_orders import vender
        vender("GGAL", 50, 1600)
        path = instance.post.call_args[0][0]
        self.assertEqual(path, "/api/v2/operar/Vender")

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_vender_payload_fields(self, mock_log, MockClient):
        instance = MockClient.return_value
        instance.post.return_value = {}
        from iol_broker.iol_orders import vender
        vender("YPFD", 50, 800)
        payload = instance.post.call_args[0][1]
        self.assertEqual(payload["simbolo"], "YPFD")
        self.assertEqual(payload["cantidad"], 50)

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_cancelar_calls_delete_with_numero(self, mock_log, MockClient):
        instance = MockClient.return_value
        instance.delete.return_value = None
        from iol_broker.iol_orders import cancelar_orden
        cancelar_orden(12345)
        instance.delete.assert_called_with("/api/v2/operaciones/12345")

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_comprar_logs_before_post(self, mock_log, MockClient):
        instance = MockClient.return_value
        instance.post.return_value = {}
        from iol_broker.iol_orders import comprar
        comprar("GGAL", 100, 1500)
        pre_logs = [c for c in mock_log.call_args_list
                    if c[0][0].get("action") == "comprar_pre"]
        self.assertGreaterEqual(len(pre_logs), 1)

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_comprar_logs_after_post(self, mock_log, MockClient):
        instance = MockClient.return_value
        instance.post.return_value = {}
        from iol_broker.iol_orders import comprar
        comprar("GGAL", 100, 1500)
        post_logs = [c for c in mock_log.call_args_list
                     if c[0][0].get("action") == "comprar_post"]
        self.assertGreaterEqual(len(post_logs), 1)

    @patch("iol_broker.iol_orders.IOLClient")
    @patch("iol_broker.iol_orders._log")
    def test_comprar_raises_on_http_error(self, mock_log, MockClient):
        from urllib.error import HTTPError
        instance = MockClient.return_value
        instance.post.side_effect = HTTPError("/test", 400, "bad", {}, None)
        from iol_broker.iol_orders import comprar
        with self.assertRaises(HTTPError):
            comprar("GGAL", 100, 1500)

    def test_format_orden_propuesta_contains_confirmacion_prompt(self):
        from iol_broker.iol_orders import format_orden_propuesta_telegram
        text = format_orden_propuesta_telegram("comprar", "GGAL", 100, 1500)
        self.assertIn("Confirmar", text)
        self.assertIn("GGAL", text)
        self.assertIn("100", text)
        self.assertIn("sí", text)


# ── Handler Tests ───────────────────────────────────────────────


class TestIOLHandler(unittest.TestCase):
    """IOL Handler routing — 6 tests."""

    @patch("iol_broker.iol_handler.get_portfolio")
    @patch("iol_broker.iol_handler.format_portfolio_telegram", return_value="Portfolio OK")
    def test_handle_portafolio_keyword(self, mock_fmt, mock_get):
        mock_get.return_value = {}
        from iol_broker.iol_handler import handle
        result = handle("mostrame mi portafolio")
        self.assertEqual(result, "Portfolio OK")
        mock_get.assert_called_once()

    @patch("iol_broker.iol_handler.get_cotizacion")
    @patch("iol_broker.iol_handler.format_cotizacion_telegram", return_value="GGAL OK")
    def test_handle_cotizacion_extracts_ticker(self, mock_fmt, mock_get):
        mock_get.return_value = {}
        from iol_broker.iol_handler import handle
        result = handle("cotización GGAL")
        mock_get.assert_called_with("bCBA", "GGAL")

    @patch("iol_broker.iol_handler.save_pending_order")
    def test_handle_comprar_returns_propuesta_not_execution(self, mock_save):
        from iol_broker.iol_handler import handle
        result = handle("comprar 100 GGAL a 1500")
        self.assertIn("Confirmar", result)
        self.assertNotIn("Orden enviada", result)

    @patch("iol_broker.iol_handler.save_pending_order")
    def test_handle_comprar_saves_pending_order_json(self, mock_save):
        from iol_broker.iol_handler import handle
        handle("comprar 100 GGAL a 1500")
        mock_save.assert_called_once()
        order = mock_save.call_args[0][0]
        self.assertEqual(order["accion"], "comprar")
        self.assertEqual(order["simbolo"], "GGAL")
        self.assertEqual(order["cantidad"], 100)

    @patch("iol_broker.iol_handler.clear_pending_order")
    @patch("iol_broker.iol_handler.comprar", return_value={"numero": 999})
    @patch("iol_broker.iol_handler.load_pending_order")
    def test_handle_confirmar_executes_pending_order(self, mock_load, mock_comprar, mock_clear):
        mock_load.return_value = {
            "accion": "comprar", "simbolo": "GGAL", "cantidad": 100,
            "precio": 1500, "mercado": "bCBA", "plazo": "t0",
        }
        from iol_broker.iol_handler import handle
        result = handle("sí confirmo")
        mock_comprar.assert_called_once()
        mock_clear.assert_called_once()
        self.assertIn("Orden enviada", result)

    def test_handle_unknown_returns_ayuda(self):
        from iol_broker.iol_handler import handle
        result = handle("algo random de iol")
        self.assertIn("portafolio", result)
        self.assertIn("cotización", result)


# ── Orchestrator Tests ──────────────────────────────────────────


class TestOrchestratorIOL(unittest.TestCase):
    """IOL keywords in orchestrator classification — 4 tests."""

    def test_iol_keyword_portafolio(self):
        from runtime.orchestrator import classify_query
        self.assertEqual(classify_query("mostrame mi portafolio"), "iol_broker")

    def test_iol_keyword_cotizacion(self):
        from runtime.orchestrator import classify_query
        self.assertEqual(classify_query("cotización GGAL"), "iol_broker")

    def test_iol_keyword_comprar(self):
        from runtime.orchestrator import classify_query
        self.assertEqual(classify_query("comprar 100 acciones de GGAL"), "iol_broker")

    def test_no_false_positive_hora(self):
        from runtime.orchestrator import classify_query
        result = classify_query("qué hora es")
        self.assertNotEqual(result, "iol_broker")


# ── Init Token Tests ────────────────────────────────────────────


class TestInitToken(unittest.TestCase):
    """IOL init token script — 6 tests."""

    _INIT_PATH = Path("/home/tenazas/tools/iol_init_token.py")

    def _load_module(self):
        """Load iol_init_token by file path (not as package)."""
        if not self._INIT_PATH.exists():
            self.skipTest("tools/ not mounted in container — run docker compose up -d after adding volume")
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "iol_init_token", str(self._INIT_PATH))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @patch("builtins.open", new_callable=mock_open, read_data="{}")
    @patch("getpass.getpass", return_value="testpass")
    @patch("builtins.input", return_value="testuser")
    def test_init_posts_to_correct_endpoint(self, mock_input, mock_getpass, mock_file):
        mod = self._load_module()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "at", "refresh_token": "rt", "expires_in": 899
        }).encode()
        with patch.object(mod, "urlopen", return_value=mock_resp) as mock_url:
            mod.main()
            req = mock_url.call_args[0][0]
            self.assertTrue(req.full_url.endswith("/token"))

    @patch("builtins.open", new_callable=mock_open, read_data="{}")
    @patch("getpass.getpass", return_value="testpass")
    @patch("builtins.input", return_value="testuser")
    def test_init_sends_password_grant(self, mock_input, mock_getpass, mock_file):
        mod = self._load_module()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "at", "refresh_token": "rt", "expires_in": 899
        }).encode()
        with patch.object(mod, "urlopen", return_value=mock_resp) as mock_url:
            mod.main()
            req = mock_url.call_args[0][0]
            self.assertIn("grant_type=password", req.data.decode())

    @patch("builtins.open", new_callable=mock_open, read_data="{}")
    @patch("getpass.getpass", return_value="testpass")
    @patch("builtins.input", return_value="testuser")
    def test_init_saves_bearer_token(self, mock_input, mock_getpass, mock_file):
        mod = self._load_module()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "SAVED_BEARER", "refresh_token": "rt", "expires_in": 899
        }).encode()
        with patch.object(mod, "urlopen", return_value=mock_resp):
            with patch.object(mod.json, "dump") as mock_dump:
                mod.main()
        saved = mock_dump.call_args[0][0]
        self.assertEqual(saved["invertironline"]["bearer_token"], "SAVED_BEARER")

    @patch("builtins.open", new_callable=mock_open, read_data="{}")
    @patch("getpass.getpass", return_value="testpass")
    @patch("builtins.input", return_value="testuser")
    def test_init_saves_refresh_token(self, mock_input, mock_getpass, mock_file):
        mod = self._load_module()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "at", "refresh_token": "SAVED_REFRESH", "expires_in": 899
        }).encode()
        with patch.object(mod, "urlopen", return_value=mock_resp):
            with patch.object(mod.json, "dump") as mock_dump:
                mod.main()
        saved = mock_dump.call_args[0][0]
        self.assertEqual(saved["invertironline"]["refresh_token"], "SAVED_REFRESH")

    @patch("builtins.open", new_callable=mock_open, read_data="{}")
    @patch("getpass.getpass", return_value="secretpass")
    @patch("builtins.input", return_value="testuser")
    def test_init_never_saves_password(self, mock_input, mock_getpass, mock_file):
        mod = self._load_module()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "access_token": "at", "refresh_token": "rt", "expires_in": 899
        }).encode()
        with patch.object(mod, "urlopen", return_value=mock_resp):
            with patch.object(mod.json, "dump") as mock_dump:
                mod.main()
        saved = json.dumps(mock_dump.call_args[0][0])
        self.assertNotIn("secretpass", saved)

    @patch("builtins.open", new_callable=mock_open, read_data="{}")
    @patch("getpass.getpass", return_value="testpass")
    @patch("builtins.input", return_value="testuser")
    def test_init_exits_on_401(self, mock_input, mock_getpass, mock_file):
        from urllib.error import HTTPError
        mod = self._load_module()
        with patch.object(mod, "urlopen", side_effect=HTTPError("/token", 401, "unauthorized", {}, None)):
            with self.assertRaises(SystemExit) as ctx:
                mod.main()
            self.assertEqual(ctx.exception.code, 1)


# ── Image Handling Tests ────────────────────────────────────────

from urllib.error import URLError as _URLError


class TestImageHandling(unittest.TestCase):
    """Telegram photo handling + Gemini vision — 6 tests."""

    @patch("runtime.telegram_handler.download_telegram_photo")
    def test_handle_photo_downloads_from_telegram(self, mock_dl):
        mock_dl.return_value = b"\xff\xd8\xff\xe0fake_jpeg"
        from runtime.telegram_handler import handle_photo
        with patch("runtime.telegram_handler._call_gemini_vision", return_value="A cat"):
            with patch("runtime.telegram_handler._update_compact_guard"):
                result = handle_photo("file123", "describe")
        mock_dl.assert_called_once_with("file123")
        self.assertIn("cat", result)

    @patch("runtime.telegram_handler.download_telegram_photo")
    def test_handle_photo_converts_to_base64(self, mock_dl):
        import base64
        raw = b"\xff\xd8\xff\xe0test"
        mock_dl.return_value = raw
        from runtime.telegram_handler import handle_photo, photo_to_base64
        expected_b64 = photo_to_base64(raw)
        with patch("runtime.telegram_handler._call_gemini_vision", return_value="ok") as mock_gemini:
            with patch("runtime.telegram_handler._update_compact_guard"):
                handle_photo("file123")
        # Gemini should have been called with the base64 version
        call_b64 = mock_gemini.call_args[0][0]
        self.assertEqual(call_b64, expected_b64)

    @patch("runtime.telegram_handler.download_telegram_photo", return_value=b"img")
    @patch("runtime.telegram_handler._update_compact_guard")
    @patch("runtime.telegram_handler.iol_analyze_image", return_value="IOL analysis")
    def test_handle_photo_routes_to_iol_on_portfolio_caption(self, mock_iol, mock_guard, mock_dl):
        from runtime.telegram_handler import handle_photo
        result = handle_photo("file123", "auditá mi portafolio")
        mock_iol.assert_called_once()
        self.assertEqual(result, "IOL analysis")

    @patch("runtime.telegram_handler.download_telegram_photo", return_value=b"img")
    @patch("runtime.telegram_handler._update_compact_guard")
    @patch("runtime.telegram_handler.analyze_image_gemini", return_value="Generic analysis")
    def test_handle_photo_routes_to_generic_on_other_caption(self, mock_generic, mock_guard, mock_dl):
        from runtime.telegram_handler import handle_photo
        result = handle_photo("file123", "qué es esto")
        mock_generic.assert_called_once()
        self.assertEqual(result, "Generic analysis")

    @patch("runtime.telegram_handler._get_gemini_key", return_value="test_key")
    @patch("runtime.telegram_handler.urlopen")
    def test_iol_analyze_image_calls_gemini_vision(self, mock_urlopen, mock_key):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{"content": {"parts": [{"text": "Portfolio analysis result"}]}}]
        }).encode()
        mock_urlopen.return_value = mock_resp

        from runtime.telegram_handler import iol_analyze_image
        result = iol_analyze_image("base64data", "audita")
        self.assertIn("Portfolio analysis", result)
        # Verify Gemini was called
        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data)
        self.assertIn("inline_data", json.dumps(payload))

    @patch("runtime.telegram_handler._get_gemini_key", return_value="test_key")
    @patch("runtime.telegram_handler.urlopen", side_effect=_URLError("offline"))
    def test_iol_analyze_image_fallback_on_gemini_error(self, mock_urlopen, mock_key):
        from runtime.telegram_handler import iol_analyze_image
        result = iol_analyze_image("base64data")
        self.assertIn("No pude analizar", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)

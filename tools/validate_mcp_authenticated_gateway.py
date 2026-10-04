"""Linux-only staging HTTPS integration probe; no external connections or production writes.

Run with a test TLS certificate/key in /tmp, isolated network and a sanitized RO cache.
Ephemeral gateway credentials exist only in this container's tmpfs.
"""

import http.client
import json
import logging
import os
import secrets
import socket
import ssl
import stat
import threading
from pathlib import Path

from workspace.runtime import mcp_readonly_gateway as gateway
from tools.validate_mcp_gateway_cache import main as validate_isolation


def main():
    validate_isolation()
    root = Path("/tmp")
    token_path = root / "gateway-token"
    token = secrets.token_hex(32)
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(token)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(root / "cert.pem", root / "key.pem")
    server = gateway.Gateway(("127.0.0.1", 0), token_path, limit=1000)
    server.tls_context = context
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client_tls = ssl.create_default_context(cafile=str(root / "cert.pem"))
    results = []
    outputs = {}
    audit = []

    class Capture(logging.Handler):
        def emit(self, record):
            audit.append(record.getMessage())

    gateway.AUDIT.addHandler(Capture())
    gateway.AUDIT.setLevel(logging.INFO)

    def call(method="tools/list", params=None, credential=token, extra=None, http_method="POST"):
        connection = http.client.HTTPSConnection("localhost", server.server_port, context=client_tls, timeout=5)
        headers = {"Content-Type": "application/json"}
        if credential is not None:
            headers["Authorization"] = "Bearer " + credential
        headers.update(extra or {})
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        try:
            connection.request(http_method, "/mcp", body, headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None
        finally:
            connection.close()

    try:
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=5):
            # A client stalled before TLS negotiation must not block another client.
            assert call("ping")[0] == 200
        results.append("stalled_TLS_does_not_block_authenticated_client")
        status, response = call("initialize", {"protocolVersion": gateway.PROTOCOL})
        assert status == 200 and response["result"]["protocolVersion"] == gateway.PROTOCOL
        results.append("TLS_verified_initialize")
        status, response = call()
        assert status == 200 and [item["name"] for item in response["result"]["tools"]] == list(gateway.adapter.TOOLS)
        results.append("exact_six_tools")
        for name in gateway.adapter.TOOLS:
            status, response = call("tools/call", {"name": name, "arguments": {}})
            assert status == 200 and not response["result"]["isError"]
            outputs[name] = json.loads(response["result"]["content"][0]["text"])
        assert outputs["get_containers"]["total"] > 0
        results.append("six_tools_real_cache")
        for invalid in (None, "invalid", token + "x"):
            status, response = call("tools/call", {"name": "get_status"}, credential=invalid)
            assert status == 401 and response == {"error": "unauthorized"}
        results.append("missing_wrong_altered_credentials_denied")
        for name in ("restart", "exec", "shell", "write", "delete", "Docker", "SSH"):
            assert "error" in call("tools/call", {"name": name})[1]
        assert "error" in call("tools/call", {"name": "get_status", "arguments": {"path": "/etc/passwd"}})[1]
        results.append("mutation_and_arbitrary_arguments_denied")
        assert call(extra={"Origin": "https://evil.example"})[0] == 403
        assert call(http_method="GET")[0] == 405
        assert call(http_method="GET", credential=None)[0] == 401
        results.append("browser_origin_and_get_denied")
        status, response = call(http_method=token)
        assert status == 501 and token not in json.dumps(response)
        results.append("HTTP_parser_errors_do_not_reflect_credentials")
        replacement = secrets.token_hex(32)
        token_path.write_text(replacement)
        assert call()[0] == 401
        assert call(credential=replacement)[0] == 200
        token_path.unlink()
        assert call(credential=replacement)[0] == 503
        results.append("rotation_revocation_missing_key_fail_closed")
        token_path.write_text(replacement)
        token_path.chmod(0o600)
        server.limit = 0
        assert call(credential=replacement)[0] == 429
        results.append("rate_limit")
        assert token not in json.dumps(outputs) and replacement not in json.dumps(outputs)
        assert token not in '\n'.join(audit) and replacement not in '\n'.join(audit)
        assert all(line.startswith("request status=") for line in audit)
        results.append("credential_free_outputs_and_audit")
        files = list(gateway.adapter.CACHE_ROOT.rglob("*.json"))
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)
        assert stat.S_IMODE(gateway.adapter.CACHE_ROOT.stat().st_mode) == 0o700
        assert all(not os.access(p, os.W_OK) for p in files)
        results.append("private_cache_700_600_readonly")
        report = {"integration_checks": results, "passed": len(results), "failed": 0,
                  "TLS": "verified certificate and hostname, minimum TLS1.2",
                  "binding": "container loopback only, no published ports"}
        if os.getenv("MCP_VALIDATE_INCLUDE_OUTPUT") == "1":
            report["_output_for_client_audit"] = outputs
        print(json.dumps(report))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        token_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

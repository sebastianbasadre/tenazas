"""VM-local TLS MCP smoke test. Reads existing private credentials without logging them."""

import argparse
import json
import ssl
from pathlib import Path
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verify(token_file, ca_file):
    token = Path(token_file).read_text().strip()
    context = ssl.create_default_context(cafile=str(ca_file))
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))

    def rpc(method, params=None):
        request = Request(
            "https://127.0.0.1:8443/mcp",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "media-verification",
                    "method": method,
                    "params": params or {},
                }
            ).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
            method="POST",
        )
        with opener.open(request, timeout=10) as response:
            raw = response.read(1024**2 + 1)
        if len(raw) > 1024**2:
            raise ValueError("Response too large")
        return json.loads(raw)

    initialized = rpc("initialize", {"protocolVersion": "2025-11-25"})
    if initialized.get("result", {}).get("protocolVersion") != "2025-11-25":
        raise ValueError("Protocol check failed")
    tools = rpc("tools/list")["result"]["tools"]
    names = {row["name"] for row in tools}
    expected = {
        "get_status",
        "get_containers",
        "get_models",
        "get_storage",
        "get_network_health",
        "get_recent_alerts",
        "get_media_library",
        "get_downloads",
    }
    if names != expected or not all(
        row["annotations"].get("readOnlyHint") is True
        and row["annotations"].get("destructiveHint") is False
        for row in tools
    ):
        raise ValueError("Tool boundary check failed")
    values = {}
    for name in sorted(names):
        result = rpc("tools/call", {"name": name, "arguments": {}})
        if "error" in result or result["result"].get("isError"):
            raise ValueError("Tool read failed")
        values[name] = json.loads(result["result"]["content"][0]["text"])
    forbidden = rpc("tools/call", {"name": "pause_download", "arguments": {}})
    if forbidden.get("error", {}).get("code") != -32602:
        raise ValueError("Mutation boundary check failed")
    library, downloads = values["get_media_library"], values["get_downloads"]
    if library["snapshot"]["state"] != "fresh" or not library["available"]:
        raise ValueError("Library not fresh")
    for row in downloads["sources"].values():
        if row["snapshot"]["state"] != "fresh" or not row["available"]:
            raise ValueError("Downloads not fresh")
    status = values["get_status"]
    if (
        status["media_library"]["data"]["totals"] != library["data"]["totals"]
        or "downloads" not in status
    ):
        raise ValueError("Status media check failed")
    return {
        "status": "VERIFIED",
        "tls_verified": True,
        "readonly_tools": len(names),
        "mutation_rejected": True,
        "generation": library["snapshot"]["generation"],
        "plex_totals": library["data"]["totals"],
        "observations": {
            "plex": library["observed_at"],
            **{s: row["observed_at"] for s, row in downloads["sources"].items()},
        },
        "downloads": {
            s: {
                "total": row["data"]["total"],
                "history_total": row["data"]["history_total"],
                "scope": row["scope"],
            }
            for s, row in downloads["sources"].items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--ca-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.token_file, args.ca_file), indent=2))
    except Exception:
        print('{"status":"VERIFICATION_FAILED_DETAILS_SUPPRESSED"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

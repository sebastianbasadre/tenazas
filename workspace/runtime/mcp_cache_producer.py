"""Trusted export step, separate from the read-only gateway and its credentials.

The gateway must receive only this sanitized directory as a read-only mount.
This module is never imported by the adapter. No automatic production hook exists.
"""

from __future__ import annotations

import json
import base64
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote

from workspace.runtime import mcp_readonly_adapter as adapter


def credential_variants(value):
    if not isinstance(value, str) or not value:
        return set()
    encoded = value.encode("utf-8")
    normal = base64.b64encode(encoded).decode("ascii")
    url_safe = base64.urlsafe_b64encode(encoded).decode("ascii")
    return {value, quote(value, safe=""), quote_plus(value), normal, normal.rstrip("="),
            url_safe, url_safe.rstrip("="), json.dumps(value, ensure_ascii=True)[1:-1]}


def _clean(value, secrets):
    if isinstance(value, dict):
        return {key: _clean(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item, secrets) for item in value]
    if isinstance(value, str):
        decoded = unquote(unquote(value))
        if any(secret in decoded for secret in secrets):
            return "[REDACTED]"
    return value


def _identifier(value, secrets):
    value = adapter._text(value)
    if not value or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", value):
        return "[REDACTED]"
    return _clean(value, secrets)


def project_cache(state, infra=None, incidents=(), *, secret_values):
    """Require an explicit credential inventory; omit raw status/title prose."""
    if secret_values is None:
        raise ValueError("An explicit credential inventory is required")
    if not isinstance(secret_values, (list, tuple, set, frozenset)) or any(
        not isinstance(value, str) for value in secret_values
    ):
        raise ValueError("Credential inventory must be a collection of values")
    secrets = tuple({variant for value in secret_values for variant in credential_variants(value)})
    # Inventory matching must precede projection/truncation, which could otherwise
    # turn a long credential into an undetectable leaked prefix.
    state = _clean(state, secrets) if isinstance(state, dict) else {}
    infra = _clean(infra, secrets) if isinstance(infra, dict) else {}
    containers = []
    for item in state.get("containers", [])[:adapter._MAX_ITEMS] if isinstance(state.get("containers"), list) else []:
        if isinstance(item, dict):
            containers.append({"name": _identifier(item.get("name", ""), secrets),
                               "up": item.get("up") is True,
                               "status": "running" if item.get("up") is True else "stopped"})
    safe = {key: adapter._project(state[key]) for key in
            ("ts", "timestamp", "model", "models", "ollama_notebook", "ollama_nas",
             "disk", "storage", "internet", "vpn", "network_devices") if key in state}
    safe["containers"] = containers
    safe_infra = {"sub": "disk", "raw": adapter._project(infra.get("raw", {}))} if infra.get("sub") == "disk" else {}
    alerts = []
    for item in list(incidents)[:20]:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        status = item.get("status")
        alerts.append({"incident_id": _identifier(item.get("incident_id", ""), secrets),
                       "title": "Infrastructure incident",
                       "severity": severity if severity in {"info", "warning", "error", "critical"} else "unknown",
                       "status": status if status in {"open", "closed", "resolved", "pending"} else "unknown"})
    result = _clean({"state": safe, "infra": safe_infra, "incidents": alerts}, secrets)
    encoded = json.dumps(result, ensure_ascii=False)
    if any(secret in encoded for secret in secrets):
        raise ValueError("Credential inventory intersects cache schema; export rejected")
    return result


def _write(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".export-", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_cache(state, infra=None, incidents=(), *, secret_values, destination=None):
    data = project_cache(state, infra, incidents, secret_values=secret_values)
    root = Path(destination or adapter.CACHE_ROOT).absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.resolve() != root or root.is_symlink():
        raise ValueError("Cache directory must not traverse links")
    os.chmod(root, 0o700)
    alerts = root / "incidents"
    alerts.mkdir(mode=0o700, exist_ok=True)
    if alerts.is_symlink():
        raise ValueError("Incident directory must not be a link")
    os.chmod(alerts, 0o700)
    _write(root / "state.json", data["state"])
    _write(root / "infra.json", data["infra"])
    for index in range(20):
        path = alerts / f"alert-{index:02}.json"
        if index < len(data["incidents"]):
            _write(path, data["incidents"][index])
        else:
            path.unlink(missing_ok=True)
    return {"cache_files": 2 + len(data["incidents"]), "containers": len(data["state"]["containers"])}

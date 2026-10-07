"""Strict sanitized JSON feed contracts; no raw NAS data or source paths in output."""

from __future__ import annotations

from datetime import UTC, datetime

from workspace.runtime.mcp_snapshot_contract import MAX_ITEMS, STATUSES, number, public_text, utc

OPTIONAL_NETWORK = {"healthy", "unhealthy", "latency_ms"}
STORAGE_FIELDS = {"total_gb", "used_gb", "free_gb", "used_pct"}
NETWORK_FIELDS = {"total", "online", "missing"} | OPTIONAL_NETWORK


def timestamp(value):
    """Normalize an existing aware observation timestamp, never replace with now."""
    if not isinstance(value, str):
        raise ValueError("Observation timestamp required")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("Aware observation timestamp required")
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def validate_feed(route, data):
    fields = {"/inventory": {"containers"}, "/storage": STORAGE_FIELDS, "/network": NETWORK_FIELDS}
    if (
        route not in fields
        or not isinstance(data, dict)
        or set(data) != fields[route] | {"observed_at"}
    ):
        raise ValueError("Invalid sanitized source fields")
    utc(data["observed_at"])
    if route == "/inventory":
        rows = data["containers"]
        if not isinstance(rows, list) or len(rows) > MAX_ITEMS:
            raise ValueError("Inventory size limit")
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != {"name", "up", "status"}
                or type(row["up"]) is not bool
                or not isinstance(row["status"], str)
                or row["status"] not in STATUSES
            ):
                raise ValueError("Invalid sanitized container")
            public_text(row["name"])
    elif route == "/storage":
        if any(not number(data[key]) for key in STORAGE_FIELDS) or data["used_pct"] > 100:
            raise ValueError("Invalid normalized storage")
        total, used, free = (data[k] for k in ("total_gb", "used_gb", "free_gb"))
        if abs(total - used - free) > max(1e-9, total * 1e-9):
            raise ValueError("Inconsistent storage totals")
        if total == 0 and (used != 0 or free != 0 or data["used_pct"] != 0):
            raise ValueError("Invalid zero storage")
        expected_pct = 100 * used / total if total else 0
        if abs(data["used_pct"] - expected_pct) > 1e-7:
            raise ValueError("Inconsistent storage percentage")
    else:
        for key in ("total", "online", "missing", "healthy", "unhealthy"):
            if key in OPTIONAL_NETWORK and data[key] is None:
                continue
            if type(data[key]) is not int or not number(data[key]):
                raise ValueError("Invalid network count")
        if data["online"] + data["missing"] != data["total"]:
            raise ValueError("Inconsistent network counts")
        for key in ("healthy", "unhealthy"):
            if data[key] is not None and data[key] > data["total"]:
                raise ValueError("Invalid network health count")
        if data["latency_ms"] is not None and not number(data["latency_ms"]):
            raise ValueError("Invalid network latency")
    return data


def storage_payload(total_bytes, used_bytes, observed_at):
    """Decimal GB (10**9 bytes), matching the _gb names; not binary GiB."""
    if not number(total_bytes) or not number(used_bytes) or used_bytes > total_bytes:
        raise ValueError("Invalid storage bytes")
    result = {
        "observed_at": timestamp(observed_at),
        "total_gb": total_bytes / 1_000_000_000,
        "used_gb": used_bytes / 1_000_000_000,
        "free_gb": (total_bytes - used_bytes) / 1_000_000_000,
        "used_pct": 100 * used_bytes / total_bytes if total_bytes else 0,
    }
    return validate_feed("/storage", result)


def network_payload(records, observed_at):
    """Same count semantics as network_exporter; never copy devices/labels."""
    if (
        not isinstance(records, list)
        or len(records) > 10000
        or any(not isinstance(row, dict) for row in records)
    ):
        raise ValueError("Invalid device inventory")
    missing = sum(bool(row.get("missing_since")) for row in records)
    return validate_feed(
        "/network",
        {
            "observed_at": timestamp(observed_at),
            "total": len(records),
            "online": len(records) - missing,
            "missing": missing,
            "healthy": None,
            "unhealthy": None,
            "latency_ms": None,
        },
    )

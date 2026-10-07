"""Strict public snapshot contract. No live collectors or runtime configuration."""

from __future__ import annotations

import ipaddress
import math
import re
from datetime import UTC, datetime

DEFAULT_TTL = 300
MAX_BYTES = 1024 * 1024
MAX_ITEMS = 100
AGGREGATES = {"total", "online", "missing", "healthy", "unhealthy", "latency_ms"}
STORAGE = {"total_gb", "used_gb", "free_gb", "used_pct"}
MODEL_FIELDS = {"name", "size", "loaded"}
STATUSES = {"running", "stopped", "healthy", "unhealthy", "unknown"}
_UNSAFE = re.compile(
    r"(?i)(token|password|passwd|secret|credential|authorization|bearer|cookie|jwt|api.?key|ssh-|-----BEGIN|https?://|[/\\]|[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}|\b\d{1,3}(?:\.\d{1,3}){3}\b|[A-Za-z0-9_+=-]{24,})"
)


def public_text(value):
    from urllib.parse import unquote

    if not isinstance(value, str) or len(value) > 120:
        raise ValueError("Invalid public text")
    decoded = unquote(unquote(value))
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", decoded) or _UNSAFE.search(decoded):
        raise ValueError("Private public text")
    for word in decoded.split():
        try:
            ipaddress.ip_address(word.strip("[](),"))
        except ValueError:
            continue
        raise ValueError("Private address")
    if ":" in decoded and (decoded.count(":") > 1 or re.search(r":\d{2,5}\b", decoded)):
        raise ValueError("Private address or port")
    return value


_CONTAINER_SYNTAX = re.compile(r"[A-Za-z0-9]{1,23}(?:[._-][A-Za-z0-9]{1,23})*")
_CONTAINER_PRIVATE = re.compile(
    r"(?i)(token|password|passwd|pwd|secret|credential|authorization|bearer|cookie|jwt|api.?key|ssh-|private.?key|session|authentication|"
    r"\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}|[0-9a-f]{2}(?:-[0-9a-f]{2}){5}|"
    r"[0-9a-f]{4}(?:\.[0-9a-f]{4}){2}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|"
    r"(?:^|[._-])(?:sk|gh[pousr]|github[._-]pat)[._-]|(?:^|[._-])(?:AKIA|ASIA)[A-Za-z0-9]{16}|AIza[A-Za-z0-9])"
)


def container_name(value):
    """Docker names only: bounded short segments, not one arbitrary long token.

    Delimiters permit descriptive long names while each alphanumeric segment keeps
    the existing 24-character opaque-string boundary. No decoding/normalization or
    generic public_text relaxation; URLs, paths, ports and encoded text fail syntax.
    """
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 120
        or not _CONTAINER_SYNTAX.fullmatch(value)
    ):
        raise ValueError("Invalid public container name")
    if _CONTAINER_PRIVATE.search(value):
        raise ValueError("Private public container name")
    return value


def utc(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise ValueError("UTC timestamp required")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 2**53


def validate_snapshot(data):
    expected = {
        "schema_version",
        "generation",
        "generated_at",
        "ttl_seconds",
        "sources",
        "containers",
        "ollama_notebook",
        "ollama_nas",
        "model",
        "disk",
        "internet",
        "vpn",
        "network_devices",
        "alerts",
    }
    if (
        not isinstance(data, dict)
        or set(data) != expected
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("Invalid snapshot schema")
    if not isinstance(data["generation"], str) or not re.fullmatch(
        r"[a-f0-9]{32}", data["generation"]
    ):
        raise ValueError("Invalid generation")
    generated = utc(data["generated_at"])
    if type(data["ttl_seconds"]) is not int or not 1 <= data["ttl_seconds"] <= 3600:
        raise ValueError("Invalid TTL")
    sources = data["sources"]
    if not isinstance(sources, dict) or set(sources) != {
        "containers",
        "models",
        "storage",
        "network",
    }:
        raise ValueError("Invalid sources")
    for source in sources.values():
        if (
            not isinstance(source, dict)
            or set(source) != {"observed_at", "available"}
            or type(source["available"]) is not bool
        ):
            raise ValueError("Invalid source")
        if source["observed_at"] is not None and utc(source["observed_at"]) > generated:
            raise ValueError("Observation after generation")
        if source["available"] and source["observed_at"] is None:
            raise ValueError("Missing observation time")
    containers = data["containers"]
    if not isinstance(containers, list) or len(containers) > MAX_ITEMS:
        raise ValueError("Invalid containers")
    for item in containers:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "up", "status"}
            or type(item["up"]) is not bool
            or not isinstance(item["status"], str)
            or item["status"] not in STATUSES
        ):
            raise ValueError("Invalid container")
        container_name(item["name"])
    for key in ("ollama_notebook", "ollama_nas", "model"):
        section = data[key]
        if not isinstance(section, dict) or set(section) - {"available", "models"}:
            raise ValueError("Invalid models")
        if "available" in section and type(section["available"]) is not bool:
            raise ValueError("Invalid availability")
        models = section.get("models", [])
        if not isinstance(models, list) or len(models) > MAX_ITEMS:
            raise ValueError("Invalid models")
        for item in models:
            if not isinstance(item, dict) or set(item) - MODEL_FIELDS or "name" not in item:
                raise ValueError("Invalid model")
            public_text(item["name"])
            if "size" in item and not number(item["size"]):
                raise ValueError("Invalid size")
            if "loaded" in item and type(item["loaded"]) is not bool:
                raise ValueError("Invalid loaded state")
    for key in ("disk", "internet", "vpn", "network_devices"):
        section = data[key]
        allowed = STORAGE if key == "disk" else AGGREGATES
        if (
            not isinstance(section, dict)
            or set(section) - allowed
            or any(
                not number(v)
                and not (
                    key != "disk" and field in {"healthy", "unhealthy", "latency_ms"} and v is None
                )
                for field, v in section.items()
            )
        ):
            raise ValueError("Invalid aggregates")
    if data["alerts"] != []:
        raise ValueError("No alert source configured")
    return data


def freshness(data, now=None, source=None):
    now = now or datetime.now(UTC)
    observed = data["generated_at"] if source is None else data["sources"][source]["observed_at"]
    available = True if source is None else data["sources"][source]["available"]
    age = (now - utc(observed)).total_seconds() if observed else None
    stale = not available or age is None or age < 0 or age > data["ttl_seconds"]
    if source is None and any(not item["available"] for item in data["sources"].values()):
        available = False
        stale = True
    if source is None and any(
        freshness(data, now, key)["state"] == "stale" for key in data["sources"]
    ):
        stale = True
    return {
        "generation": data["generation"],
        "generated_at": data["generated_at"],
        "observed_at": observed,
        "age_seconds": age,
        "ttl_seconds": data["ttl_seconds"],
        "state": "stale" if stale else "fresh",
        "available": available,
    }

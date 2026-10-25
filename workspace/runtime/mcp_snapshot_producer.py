"""Read-only source collection, single-writer snapshot publication and explicit CLI."""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from workspace.runtime import mcp_cache_policy as policy
from workspace.runtime.mcp_snapshot_contract import (
    DEFAULT_TTL,
    MAX_BYTES,
    MAX_ITEMS,
    public_text,
    validate_snapshot,
)
from workspace.runtime.mcp_source_contract import NETWORK_FIELDS, STORAGE_FIELDS, validate_feed


class ReadOnlyHTTP:
    """GET only; no ambient proxies, credentials, redirect following or error-body logging."""

    def __init__(self, timeout=5, allowed_urls=()):
        from urllib.request import HTTPRedirectHandler

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.timeout = timeout
        self.allowed_urls = frozenset(allowed_urls)

    def get(self, url):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid source URL")
        if url not in self.allowed_urls:
            raise ValueError("Source not allowlisted")
        with self.opener.open(Request(url, method="GET"), timeout=self.timeout) as response:
            payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("Source size limit")
        return payload


class Sources:
    """Explicit source configuration; never imports Tenazas secrets or live collectors."""

    def __init__(
        self,
        *,
        fetch,
        inventory_url,
        storage_url,
        network_url,
        ollama_url,
    ):
        self.fetch = fetch
        self.inventory_url = inventory_url
        self.storage_url = storage_url
        self.network_url = network_url
        self.ollama_url = ollama_url.rstrip("/")

    def containers(self):
        data = validate_feed("/inventory", json.loads(self.fetch(self.inventory_url)))
        return {"containers": data["containers"]}, data["observed_at"]

    def models(self):
        tags = json.loads(self.fetch(self.ollama_url + "/api/tags"))
        running = json.loads(self.fetch(self.ollama_url + "/api/ps"))
        if not isinstance(tags, dict) or not isinstance(running, dict):
            raise ValueError("Invalid models response")
        models, loaded = tags.get("models"), running.get("models")
        if (
            not isinstance(models, list)
            or not isinstance(loaded, list)
            or max(len(models), len(loaded)) > MAX_ITEMS
        ):
            raise ValueError("Invalid models response")
        loaded_names = {public_text(item["name"]) for item in loaded}
        items = [
            {
                "name": public_text(item["name"]),
                "size": item["size"],
                "loaded": item["name"] in loaded_names,
            }
            for item in models
        ]
        return {
            "ollama_notebook": {"available": True, "models": items},
            "ollama_nas": {},
            "model": {},
        }

    def storage(self):
        data = validate_feed("/storage", json.loads(self.fetch(self.storage_url)))
        return {"disk": {k: data[k] for k in STORAGE_FIELDS}}, data["observed_at"]

    def network(self):
        data = validate_feed("/network", json.loads(self.fetch(self.network_url)))
        return {
            "internet": {},
            "vpn": {},
            "network_devices": {k: data[k] for k in NETWORK_FIELDS},
        }, data["observed_at"]


def build_snapshot(collectors, previous=None, *, now=None, ttl=DEFAULT_TTL):
    def stamp():
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError("Aware UTC clock required")
        return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")

    state = {
        "schema_version": 1,
        "generation": uuid.uuid4().hex,
        "generated_at": stamp(),
        "ttl_seconds": ttl,
        "sources": {
            s: {"available": False, "observed_at": None}
            for s in ("containers", "models", "storage", "network")
        },
        "containers": [],
        "ollama_notebook": {},
        "ollama_nas": {},
        "model": {},
        "disk": {},
        "internet": {},
        "vpn": {},
        "network_devices": {},
        "alerts": [],
    }
    if previous is not None:
        previous = validate_snapshot(previous)
    groups = {
        "containers": {"containers"},
        "models": {"ollama_notebook", "ollama_nas", "model"},
        "storage": {"disk"},
        "network": {"internet", "vpn", "network_devices"},
    }
    for source, keys in groups.items():
        if previous:
            for key in keys:
                state[key] = copy.deepcopy(previous[key])
            state["sources"][source]["observed_at"] = previous["sources"][source]["observed_at"]
        try:
            value = collectors[source]()
            observed = stamp()
            if isinstance(value, tuple):
                value, observed = value
            if not isinstance(value, dict) or set(value) != keys:
                raise ValueError("Invalid source fields")
            candidate = copy.deepcopy(state)
            candidate.update(value)
            candidate["generated_at"] = stamp()
            candidate["sources"][source] = {"available": True, "observed_at": observed}
            validate_snapshot(candidate)
            state = candidate
        except Exception:
            # Fixed public availability flag only; never serialize source exceptions.
            state["sources"][source]["available"] = False
    state["generated_at"] = stamp()
    return validate_snapshot(state)


class ProducerBusy(Exception):
    pass


@contextmanager
def writer_lock(root):
    """Linux advisory lock held across collection and publication; never unlinked."""
    if os.name != "posix":
        raise ValueError("Linux writer required")
    import fcntl

    root = Path(root).absolute()
    if root.resolve(strict=True) != root:
        raise ValueError("Unsafe cache path")
    policy.validate_entry(root, True)
    uid, gid = policy.expected_identity()
    if (os.geteuid(), os.getegid()) != (uid, gid):
        raise ValueError("Producer identity mismatch")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        descriptor = os.open(
            ".producer.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=directory,
        )
        info = os.fstat(descriptor)
        import stat

        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size != 0
            or (info.st_uid, info.st_gid) != (uid, gid)
        ):
            raise ValueError("Unsafe writer lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProducerBusy() from exc
        os.fsync(directory)
        yield root
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _atomic_replace(root, snapshot, *, initial=False):
    """Publish into an already provisioned Linux cache, as tenazas-mcp:tenazas-mcp.

    A single scheduled writer is required. No directory or ownership provisioning.
    """
    data = json.dumps(validate_snapshot(snapshot), allow_nan=False, separators=(",", ":")).encode()
    if len(data) > MAX_BYTES:
        raise ValueError("Snapshot size limit")
    root = Path(root).absolute()
    if initial:
        if {p.name for p in root.iterdir()} != {".producer.lock", "infra.json", "incidents"}:
            raise ValueError("Unsafe bootstrap layout")
        policy.validate_entry(root, True)
        policy.validate_entry(root / "infra.json")
        policy.validate_entry(root / "incidents", True)
    else:
        policy.validate_cache(root)
    if any(root.glob(".state-*.tmp")):
        raise ValueError("Unfinished publication")
    uid, gid = policy.expected_identity()
    if (os.geteuid(), os.getegid()) != (uid, gid):
        raise ValueError("Producer identity mismatch")
    path = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=root)
        path = Path(temporary)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        policy.validate_entry(path)
        validate_snapshot(json.loads(path.read_bytes()))
        os.replace(path, root / "state.json")
        path = None
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def produce_once(root, sources, *, now=None, ttl=DEFAULT_TTL):
    """One scheduled cycle; lock covers previous state, collection and publication."""
    from workspace.runtime.mcp_readonly_adapter import _read_snapshot

    root = Path(root).absolute()
    with writer_lock(root):
        policy.validate_cache(root)
        previous = validate_snapshot(_read_snapshot(root / "state.json", None))
        collectors = {
            key: getattr(sources, key) for key in ("containers", "models", "storage", "network")
        }
        snapshot = build_snapshot(collectors, previous, now=now, ttl=ttl)
        _atomic_replace(root, snapshot)
        return snapshot["generation"]


def atomic_replace(root, snapshot):
    validate_snapshot(snapshot)
    with writer_lock(root):
        _atomic_replace(root, snapshot)


def initialize_cache(root):
    """Offline seed only; operator must provision an empty 0700 dedicated directory."""
    with writer_lock(root) as root:
        if {p.name for p in root.iterdir()} != {".producer.lock"}:
            raise ValueError("Cache must be empty")
        (root / "incidents").mkdir(mode=0o700)
        fd = os.open(
            root / "infra.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"{}")
            stream.flush()
            os.fsync(stream.fileno())
        _atomic_replace(root, build_snapshot({}), initial=True)
        policy.validate_cache(root)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def configured_sources(config):
    """No defaults for real URLs, credentials or metric names. No environment reads."""
    required = {
        "inventory_url",
        "storage_url",
        "network_url",
        "ollama_url",
    }
    if not isinstance(config, dict) or set(config) != required | {"ttl_seconds", "timeout_seconds"}:
        raise ValueError("Invalid producer configuration")
    ttl, timeout = config["ttl_seconds"], config["timeout_seconds"]
    import math

    if (
        type(ttl) is not int
        or not 1 <= ttl <= 3600
        or type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 0 < timeout <= 30
    ):
        raise ValueError("Invalid producer limits")
    for key in ("inventory_url", "storage_url", "network_url", "ollama_url"):
        url = config[key]
        if not isinstance(url, str) or any(ord(c) <= 32 for c in url):
            raise ValueError("Invalid source URL")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid source URL")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Invalid source port")
    base = config["ollama_url"].rstrip("/")
    urls = (
        config["inventory_url"],
        config["storage_url"],
        config["network_url"],
        base + "/api/tags",
        base + "/api/ps",
    )
    http = ReadOnlyHTTP(timeout=timeout, allowed_urls=urls)
    return Sources(fetch=http.get, **{key: config[key] for key in required}), ttl


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--config-file", type=Path)
    parser.add_argument("--initialize", action="store_true")
    args = parser.parse_args(argv)
    audit = logging.getLogger("tenazas.mcp.producer")
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    try:
        if args.initialize:
            if args.config_file:
                raise ValueError()
            initialize_cache(args.cache_root)
        else:
            if not args.config_file:
                raise ValueError()
            with args.config_file.open("rb") as stream:
                configuration = stream.read(65537)
            if len(configuration) > 65536:
                raise ValueError()
            sources, ttl = configured_sources(json.loads(configuration))
            produce_once(args.cache_root, sources, ttl=ttl)
        audit.info("snapshot_cycle_complete")
        return 0
    except ProducerBusy:
        audit.info("snapshot_writer_busy")
        return 2
    except Exception:
        audit.error("snapshot_cycle_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

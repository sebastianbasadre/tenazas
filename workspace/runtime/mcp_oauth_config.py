"""Explicit proxy configuration; no staging defaults or secret values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REQUIRED_SCOPE = "read:tenazas"


def https_url(value):
    if (
        not isinstance(value, str)
        or any(ord(c) <= 32 for c in value)
        or any(c in value for c in ('"', "\\", "<", ">"))
    ):
        raise ValueError("Invalid HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid HTTPS URL")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid HTTPS port")
    return value


@dataclass(frozen=True)
class ProxyConfig:
    issuer: str
    audience: str
    jwks_url: str
    upstream_mcp: str
    allowed_upstreams: tuple[str, ...]
    resource_metadata: str
    internal_bearer_file: Path
    bearer_uid: int
    bearer_gid: int
    connect_timeout: float = 3
    read_timeout: float = 5
    client_timeout: float = 5
    request_limit: int = 16384
    response_limit: int = 1024 * 1024
    concurrency: int = 16
    jwks_ttl: int = 300
    request_rate: int = 120
    rate_window: float = 60

    def __post_init__(self):
        for value in (self.issuer, self.jwks_url, self.upstream_mcp, self.resource_metadata):
            https_url(value)
        if (
            not isinstance(self.audience, str)
            or not self.audience
            or any(ord(c) < 32 for c in self.audience)
        ):
            raise ValueError("Invalid audience")
        if (
            not self.allowed_upstreams
            or any(https_url(url) != url for url in self.allowed_upstreams)
            or self.upstream_mcp not in self.allowed_upstreams
        ):
            raise ValueError("Upstream not allowlisted")
        if not Path(self.internal_bearer_file).is_absolute():
            raise ValueError("Absolute bearer path required")
        for identity in (self.bearer_uid, self.bearer_gid):
            if type(identity) is not int or identity < 0:
                raise ValueError("Invalid bearer identity")
        for size in (
            self.request_limit,
            self.response_limit,
            self.concurrency,
            self.jwks_ttl,
            self.request_rate,
        ):
            if type(size) is not int or size <= 0:
                raise ValueError("Invalid limit")
        import math

        for timeout in (
            self.connect_timeout,
            self.read_timeout,
            self.client_timeout,
            self.rate_window,
        ):
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("Invalid timeout")

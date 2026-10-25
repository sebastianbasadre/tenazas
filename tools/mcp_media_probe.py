"""NAS-local, read-only source verification. Prints no credentials or media titles.

No service changes, download operations, session login or credential files created.
Tautulli's existing API key stays in NAS process memory and a local POST body.
"""

import argparse
import configparser
import ipaddress
import json
import os
import re
import ssl
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

MAX_BYTES = 2 * 1024 * 1024
COMMANDS = frozenset({"get_server_identity", "get_libraries", "get_activity"})
APIS = frozenset(
    {
        "SYNO.API.Auth",
        "SYNO.DownloadStation.Info",
        "SYNO.DownloadStation.Task",
        "SYNO.DownloadStation.Statistic",
        "SYNO.DownloadStation2.Task",
    }
)


class ProbeError(Exception):
    """Only fixed diagnostic codes may cross the output boundary."""


def require(ok, code):
    if not ok:
        raise ProbeError(code)


def local_url(value, loopback=False):
    try:
        parts = urlsplit(value)
        address = ipaddress.ip_address(parts.hostname)
        _ = parts.port
    except (ValueError, TypeError):
        raise ProbeError("INVALID_LOCAL_URL") from None
    allowed = (
        address.is_loopback
        if loopback
        else (
            address.is_loopback
            or any(
                address in net
                for net in (
                    ipaddress.ip_network("10.0.0.0/8"),
                    ipaddress.ip_network("172.16.0.0/12"),
                    ipaddress.ip_network("192.168.0.0/16"),
                )
            )
        )
    )
    require(
        parts.scheme in {"http", "https"}
        and allowed
        and not parts.username
        and not parts.password
        and not parts.query
        and not parts.fragment
        and parts.path in {"", "/"},
        "INVALID_LOCAL_URL",
    )
    return value.rstrip("/")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HTTP:
    def __init__(self, ca_file=None, timeout=8):
        require(type(timeout) is int and 1 <= timeout <= 60, "INVALID_HTTP_TIMEOUT")
        self.timeout = timeout
        context = ssl.create_default_context(cafile=ca_file)
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))

    def request(self, url, form=None):
        body = None if form is None else urlencode(form).encode()
        request = Request(url, data=body, method="GET" if body is None else "POST")
        if body is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_BYTES + 1)
            require(len(raw) <= MAX_BYTES, "RESPONSE_TOO_LARGE")
            value = json.loads(raw)
            require(isinstance(value, dict), "INVALID_RESPONSE")
            return value
        except HTTPError as error:
            raise ProbeError("HTTP_REDIRECT" if 300 <= error.code < 400 else "HTTP_ERROR") from None
        except (URLError, TimeoutError, OSError):
            raise ProbeError("SOURCE_UNREACHABLE_OR_TLS_ERROR") from None
        except (ValueError, UnicodeError, RecursionError):
            raise ProbeError("INVALID_RESPONSE") from None


def read_config(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES, "UNSAFE_CONFIG")
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "UNSAFE_CONFIG")
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(raw.decode("utf-8-sig"))
        general = parser["General"]
        key = general.get("api_key", "").strip().strip("\"'")
        require(re.fullmatch(r"[A-Za-z0-9]{16,128}", key), "API_KEY_MISSING_OR_INVALID")
        require(general.get("api_enabled", "1") == "1", "API_DISABLED")
        prefix = general.get("http_root", "").strip().strip("\"'").rstrip("/")
        require(
            not prefix
            or (
                prefix.startswith("/")
                and re.fullmatch(r"/[A-Za-z0-9_/-]+", prefix)
                and ".." not in prefix.split("/")
            ),
            "INVALID_HTTP_ROOT",
        )
        return (
            key,
            prefix,
            general.get("http_port", "8181"),
            general.get("enable_https", "0") == "1",
        )
    except (configparser.Error, KeyError, UnicodeError):
        raise ProbeError("INVALID_TAUTULLI_CONFIG") from None


def inspect_container(name):
    require(
        isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name),
        "INVALID_CONTAINER_REFERENCE",
    )
    result = subprocess.run(
        ["/usr/local/bin/docker", "inspect", name],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    objects = json.loads(result.stdout)
    require(isinstance(objects, list) and len(objects) == 1, "INVALID_CONTAINER_METADATA")
    return objects[0]


def discover_tautulli():
    container = inspect_container("tautulli")
    require(container["State"]["Running"], "TAUTULLI_NOT_RUNNING")
    mounts = [m for m in container.get("Mounts", []) if m.get("Destination") == "/config"]
    require(len(mounts) == 1, "TAUTULLI_CONFIG_LOCATION_REQUIRED")
    return Path(mounts[0]["Source"]) / "config.ini", container


def network_namespace_owner(container):
    """Follow Docker's shared namespace relation; retain Tautulli's own config."""
    visited = set()
    for _ in range(8):
        mode = container.get("HostConfig", {}).get("NetworkMode", "")
        if not mode.startswith("container:"):
            return container
        reference = mode.split(":", 1)[1]
        require(reference not in visited, "CONTAINER_NETWORK_CYCLE")
        visited.add(reference)
        container = inspect_container(reference)
        require(container.get("State", {}).get("Running") is True, "NETWORK_OWNER_NOT_RUNNING")
    raise ProbeError("CONTAINER_NETWORK_CHAIN_TOO_LONG")


def tautulli_call(http, endpoint, key, command):
    require(command in COMMANDS, "COMMAND_FORBIDDEN")
    value = http.request(endpoint, {"apikey": key, "cmd": command})
    response = value.get("response")
    require(
        isinstance(response, dict) and response.get("result") == "success", "TAUTULLI_API_ERROR"
    )
    require("data" in response, "INVALID_RESPONSE")
    return response["data"]


def summarize_tautulli(identity, libraries, activity):
    identities = identity if isinstance(identity, list) else [identity]
    require(
        len(identities) == 1
        and isinstance(identities[0], dict)
        and bool(identities[0].get("machine_identifier")),
        "PLEX_IDENTITY_UNAVAILABLE",
    )
    require(isinstance(libraries, list) and len(libraries) <= 100, "INVALID_LIBRARIES")
    require(
        isinstance(activity, dict)
        and isinstance(activity.get("sessions"), list)
        and len(activity["sessions"]) <= 100,
        "INVALID_ACTIVITY",
    )
    kinds = {kind: 0 for kind in ("movie", "show", "artist", "photo", "other")}
    for row in libraries:
        require(isinstance(row, dict), "INVALID_LIBRARIES")
        kind = row.get("section_type")
        kinds[kind if kind in kinds else "other"] += 1
    return {
        "status": "VERIFIED",
        "plex_identity_available": True,
        "libraries_by_type": kinds,
        "active_sessions": len(activity["sessions"]),
        "library_counts_live_verified": False,
    }


def tautulli_origin(container, port, https):
    """Use loopback publication or one private IP from this container's metadata.

    Never publish a port or select an arbitrary network destination to send a key.
    Containers with multiple private addresses require an explicit routing review.
    """
    require(str(port).isdigit() and 1 <= int(port) <= 65535, "INVALID_TAUTULLI_PORT")
    if container:
        container = network_namespace_owner(container)
    scheme = "https" if https else "http"
    if not container or container.get("HostConfig", {}).get("NetworkMode") == "host":
        return f"{scheme}://127.0.0.1:{port}"
    settings = container.get("NetworkSettings", {})
    bindings = settings.get("Ports", {}).get(str(port) + "/tcp") or []
    bindings = [b for b in bindings if b.get("HostIp") in {"0.0.0.0", "127.0.0.1", "::"}]
    if bindings:
        host_port = bindings[0].get("HostPort", "")
        require(str(host_port).isdigit() and 1 <= int(host_port) <= 65535, "INVALID_TAUTULLI_PORT")
        return f"{scheme}://127.0.0.1:{host_port}"
    candidates = set()
    for network in (settings.get("Networks") or {}).values():
        ip = network.get("IPAddress")
        if not ip:
            continue
        try:
            candidates.add(local_url(f"{scheme}://{ip}:{port}"))
        except ProbeError:
            continue
    require(len(candidates) == 1, "TAUTULLI_LOCAL_ROUTE_REQUIRES_REVIEW")
    return candidates.pop()


def probe_tautulli(http, config_path=None, base_url=None):
    container = None
    if config_path is None:
        config_path, container = discover_tautulli()
    key, prefix, port, https = read_config(config_path)
    if base_url is None:
        base_url = tautulli_origin(container, port, https)
    else:
        base_url = local_url(base_url, loopback=True)
    endpoint = base_url + prefix + "/api/v2"
    identity = tautulli_call(http, endpoint, key, "get_server_identity")
    libraries = tautulli_call(http, endpoint, key, "get_libraries")
    activity = tautulli_call(http, endpoint, key, "get_activity")
    return summarize_tautulli(identity, libraries, activity)


def summarize_api_info(value):
    require(
        value.get("success") is True and isinstance(value.get("data"), dict), "DSM_API_INFO_ERROR"
    )
    result = {}
    for name, descriptor in value["data"].items():
        if name not in APIS:
            continue
        require(isinstance(descriptor, dict), "INVALID_API_DESCRIPTOR")
        minimum, maximum = descriptor.get("minVersion"), descriptor.get("maxVersion")
        path = descriptor.get("path")
        require(
            type(minimum) is int and type(maximum) is int and 1 <= minimum <= maximum <= 1000,
            "INVALID_API_VERSION",
        )
        require(
            isinstance(path, str)
            and re.fullmatch(r"[A-Za-z0-9_/-]+\.cgi", path)
            and ".." not in path.split("/"),
            "INVALID_API_PATH",
        )
        result[name] = {"path": path, "min_version": minimum, "max_version": maximum}
    return {
        "status": "API_DISCOVERED"
        if "SYNO.DownloadStation.Task" in result
        else "TASK_API_NOT_ADVERTISED",
        "apis": result,
        "authenticated_task_read_verified": False,
    }


def probe_download_station(http, base_url):
    url = (
        local_url(base_url)
        + "/webapi/query.cgi?"
        + urlencode(
            {
                "api": "SYNO.API.Info",
                "version": 1,
                "method": "query",
                "query": "all",
            }
        )
    )
    return summarize_api_info(http.request(url))


def safe_probe(function):
    try:
        result = function()
    except ProbeError as error:
        result = {"status": "NOT_VERIFIED", "code": error.args[0]}
    except Exception:
        result = {"status": "NOT_VERIFIED", "code": "LOCAL_PROBE_ERROR_DETAILS_SUPPRESSED"}
    return {**result, "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", required=True)
    parser.add_argument("--tautulli-config", type=Path)
    parser.add_argument("--tautulli-url")
    parser.add_argument("--dsm-url", default="http://127.0.0.1:5000")
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--timeout", type=int, choices=range(1, 61), default=8, metavar="SECONDS")
    args = parser.parse_args()
    http = HTTP(args.ca_file, timeout=args.timeout)
    output = {
        "tautulli": safe_probe(
            lambda: probe_tautulli(http, args.tautulli_config, args.tautulli_url)
        ),
        "download_station": safe_probe(lambda: probe_download_station(http, args.dsm_url)),
        "credentials_printed": False,
        "services_modified": False,
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

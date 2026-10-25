"""Strict sanitized media projection shared by NAS collectors and the MCP reader."""

import math
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import unquote

MAX_BYTES = 1024 * 1024
SOURCES = ("plex", "download_station", "sabnzbd")
STATES = {
    "queued",
    "downloading",
    "paused",
    "checking",
    "finishing",
    "completed",
    "seeding",
    "extracting",
    "processing",
    "error",
    "unknown",
}
CODES = {None, "SOURCE_UNAVAILABLE", "AUTH_FAILED", "INVALID_RESPONSE"}


def require(value):
    if not value:
        raise ValueError("Invalid media projection")


def title(value):
    """Dedicated Unicode media title policy; never changes generic public_text."""
    if not isinstance(value, str):
        return "[redacted]"
    value = unicodedata.normalize("NFC", value).strip()
    decoded = value
    for _ in range(3):
        decoded = unquote(decoded)
    unsafe = re.search(
        r"(?i)(?:https?[:/]|magnet:|ftp:|www\.|[\\/]|[\w.+-]+@[\w.-]+|"
        r"(?:token|password|passwd|apikey|api.key|secret|authorization|cookie)\s*[:=]|"
        r"bearer\s+|-----BEGIN|ssh-(?:rsa|ed25519)|\b[A-Za-z0-9_+=-]{32,}\b)",
        decoded,
    )
    if (
        not value
        or len(value) > 240
        or unsafe
        or any(unicodedata.category(c)[0] == "C" for c in decoded)
    ):
        return "[redacted]"
    return value


def stamp():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def utc(value):
    require(isinstance(value, str) and value.endswith("Z") and len(value) <= 40)
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.utcoffset().total_seconds() == 0)
    return result


def number(value, upper=None):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0)
    require(upper is None or value <= upper)


def exact(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields.split()))


def count(value):
    require(type(value) is int and 0 <= value <= 10**12)


def safe_title(value):
    require(isinstance(value, str) and title(value) == value)


def validate_library(data):
    exact(
        data,
        "libraries totals recent recent_limit recent_truncated active_playback excluded_library_types counts_live_verified",
    )
    require(data["counts_live_verified"] is True)
    require(isinstance(data["libraries"], list) and len(data["libraries"]) <= 100)
    totals = {"movies": 0, "series": 0, "episodes": 0}
    for row in data["libraries"]:
        exact(row, "title type movies series episodes")
        safe_title(row["title"])
        require(row["type"] in {"movie", "show"})
        for k in totals:
            count(row[k])
            totals[k] += row[k]
        require(
            row["series"] == row["episodes"] == 0 if row["type"] == "movie" else row["movies"] == 0
        )
    require(data["totals"] == totals)
    exact(data["excluded_library_types"], "artist photo other")
    for v in data["excluded_library_types"].values():
        count(v)
    require(type(data["recent_limit"]) is int and 1 <= data["recent_limit"] <= 100)
    require(type(data["recent_truncated"]) is bool)
    for field in ("recent", "active_playback"):
        rows = data[field]
        require(
            isinstance(rows, list)
            and len(rows) <= (data["recent_limit"] if field == "recent" else 100)
        )
        for row in rows:
            exact(row, "title type added_at" if field == "recent" else "title type state")
            safe_title(row["title"])
            require(row["type"] in {"movie", "show", "episode"})
            if field == "recent":
                utc(row["added_at"])
            else:
                require(row["state"] in {"playing", "paused", "buffering", "unknown"})
    return data


def validate_downloads(data):
    exact(
        data,
        "tasks total returned truncated history history_total history_truncated speed_bytes_per_second",
    )
    if data["speed_bytes_per_second"] is not None:
        number(data["speed_bytes_per_second"], 10**18)
    count(data["total"])
    count(data["returned"])
    count(data["history_total"])
    require(type(data["truncated"]) is bool and type(data["history_truncated"]) is bool)
    require(data["returned"] == len(data["tasks"]) <= data["total"])
    require(data["truncated"] == (data["returned"] < data["total"]))
    require(data["history_truncated"] == (len(data["history"]) < data["history_total"]))
    for key in ("tasks", "history"):
        require(isinstance(data[key], list) and len(data[key]) <= 1000)
        for row in data[key]:
            exact(
                row,
                "title state progress_percent size_bytes downloaded_bytes speed_bytes_per_second eta_seconds eta_estimated native_status_code",
            )
            safe_title(row["title"])
            require(row["state"] in STATES)
            if row["progress_percent"] is not None:
                number(row["progress_percent"], 100)
            for k in ("size_bytes", "downloaded_bytes", "speed_bytes_per_second", "eta_seconds"):
                if row[k] is not None:
                    number(row[k], 10**18)
            require(type(row["eta_estimated"]) is bool)
            require(row["native_status_code"] is None or type(row["native_status_code"]) is int)
            if not row["speed_bytes_per_second"] or not row["size_bytes"]:
                require(row["eta_seconds"] is None)
    return data


def validate_state(value):
    exact(value, "schema_version generation generated_at ttl_seconds sources")
    require(value["schema_version"] == 1 and type(value["schema_version"]) is int)
    require(
        isinstance(value["generation"], str) and re.fullmatch(r"[a-f0-9]{32}", value["generation"])
    )
    generated = utc(value["generated_at"])
    require(type(value["ttl_seconds"]) is int and 1 <= value["ttl_seconds"] <= 3600)
    require(isinstance(value["sources"], dict) and set(value["sources"]) == set(SOURCES))
    for source, row in value["sources"].items():
        exact(row, "available observed_at code scope data")
        require(type(row["available"]) is bool and row["code"] in CODES)
        require(
            row["scope"]
            == {
                "plex": "local_plex_server",
                "download_station": "DSM user sbasadre; visible tasks only",
                "sabnzbd": "SABnzbd instance queue and history",
            }[source]
        )
        if row["observed_at"] is not None:
            require(utc(row["observed_at"]) <= generated)
        if row["available"]:
            require(
                row["observed_at"] is not None and row["code"] is None and row["data"] is not None
            )
        if row["data"] is not None:
            require(row["observed_at"] is not None)
            (validate_library if source == "plex" else validate_downloads)(row["data"])
    return value


def view(state, source, now=None):
    row = state["sources"][source]
    now = now or datetime.now(UTC)
    age = (now - utc(row["observed_at"])).total_seconds() if row["observed_at"] else None
    return {
        **row,
        "snapshot": {
            "generation": state["generation"],
            "generated_at": state["generated_at"],
            "observed_at": row["observed_at"],
            "age_seconds": age,
            "ttl_seconds": state["ttl_seconds"],
            "available": row["available"],
            "state": "fresh"
            if row["available"] and age is not None and 0 <= age <= state["ttl_seconds"]
            else "stale",
        },
    }

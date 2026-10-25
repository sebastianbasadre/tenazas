"""NAS-only read-only media collector. Credentials never leave local memory.

Only fixed Plex GETs, SAB queue/history POSTs and DSM list operations are allowed.
No imported module starts collection. Public files contain validated projections.
"""

import argparse
import copy
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request

from workspace.runtime.mcp_media_contract import (
    SOURCES,
    stamp,
    title,
    validate_downloads,
    validate_library,
    validate_state,
)

from tools.mcp_media_probe import HTTP, MAX_BYTES, inspect_container, tautulli_origin

SCOPES = {
    "plex": "local_plex_server",
    "download_station": "DSM user sbasadre; visible tasks only",
    "sabnzbd": "SABnzbd instance queue and history",
}
DS_STATES = {
    1: "queued",
    2: "downloading",
    3: "paused",
    4: "finishing",
    5: "completed",
    6: "checking",
    7: "seeding",
    8: "seeding",
    9: "queued",
    10: "extracting",
    11: "processing",
    12: "processing",
    13: "completed",
    14: "processing",
    15: "paused",
}
SAB_STATES = {
    "Queued": "queued",
    "Downloading": "downloading",
    "Paused": "paused",
    "Checking": "checking",
    "Verifying": "checking",
    "Repairing": "processing",
    "Extracting": "extracting",
    "Moving": "finishing",
    "Running": "processing",
    "Completed": "completed",
    "Failed": "error",
    "Fetching": "queued",
}
PLEX_PREFS = Path("/volume1/PlexMediaServer/AppData/Plex Media Server/Preferences.xml")


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("Invalid local configuration")
        return stream.read(MAX_BYTES + 1)


class Plex:
    def __init__(self):
        prefs = ET.fromstring(read_private(PLEX_PREFS))
        self.token = prefs.get("PlexOnlineToken")
        self.identity = prefs.get("ProcessedMachineIdentifier")
        if not self.token or not self.identity:
            raise ValueError("Plex credentials unavailable")
        self.http = HTTP(timeout=10)

    def get(self, path):
        if not re.fullmatch(
            r"/(?:identity|status/sessions|library/sections(?:/[0-9]+/(?:all|recentlyAdded))?)(?:\?[A-Za-z0-9=&-]+)?",
            path,
        ):
            raise ValueError("Plex route forbidden")
        request = Request(
            "http://127.0.0.1:32400" + path, headers={"X-Plex-Token": self.token}, method="GET"
        )
        with self.http.opener.open(request, timeout=10) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            raise ValueError("Invalid Plex XML")
        return ET.fromstring(raw)

    def collect(self):
        if self.get("/identity").get("machineIdentifier") != self.identity:
            raise ValueError("Plex identity mismatch")
        sections = self.get("/library/sections")
        if len(sections) > 100:
            raise ValueError("Too many libraries")
        libraries, recent = [], []
        totals = {"movies": 0, "series": 0, "episodes": 0}
        excluded = {"artist": 0, "photo": 0, "other": 0}
        truncated = False
        for section in sections:
            kind, section_id = section.get("type"), section.get("key", "")
            if kind not in {"movie", "show"}:
                excluded[kind if kind in excluded else "other"] += 1
                continue
            if not section_id.isdigit():
                raise ValueError("Invalid section")
            row = {
                "title": title(section.get("title")),
                "type": kind,
                "movies": 0,
                "series": 0,
                "episodes": 0,
            }
            for field, plex_type in (
                (("movies", 1),) if kind == "movie" else (("series", 2), ("episodes", 4))
            ):
                counts = self.get(
                    f"/library/sections/{section_id}/all?type={plex_type}&X-Plex-Container-Start=0&X-Plex-Container-Size=0"
                )
                row[field] = int(counts.attrib["totalSize"])
                totals[field] += row[field]
            libraries.append(row)
            items = self.get(
                f"/library/sections/{section_id}/recentlyAdded?X-Plex-Container-Start=0&X-Plex-Container-Size=10"
            )
            truncated |= int(items.get("totalSize", str(len(items)))) > len(items)
            for item in items:
                media_type = item.get("type")
                if media_type not in {"movie", "show", "episode"}:
                    continue
                recent.append(
                    {
                        "title": title(item.get("title")),
                        "type": media_type,
                        "added_at": datetime.fromtimestamp(int(item.attrib["addedAt"]), UTC)
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                )
        sessions = self.get("/status/sessions")
        if len(sessions) > 100:
            raise ValueError("Too many sessions")
        activity = []
        for item in sessions:
            if item.get("type") not in {"movie", "episode", "show"}:
                continue
            player = item.find("Player")
            state = player.get("state") if player is not None else "unknown"
            activity.append(
                {
                    "title": title(item.get("title")),
                    "type": item.get("type"),
                    "state": state if state in {"playing", "paused", "buffering"} else "unknown",
                }
            )
        recent.sort(key=lambda row: row["added_at"], reverse=True)
        return validate_library(
            {
                "libraries": libraries,
                "totals": totals,
                "recent": recent[:20],
                "recent_limit": 20,
                "recent_truncated": truncated or len(recent) > 20,
                "active_playback": activity,
                "excluded_library_types": excluded,
                "counts_live_verified": True,
            }
        )


def download(title_value, state, size, downloaded, speed, native=None):
    size = max(0, float(size)) if size is not None else None
    downloaded = max(0, float(downloaded)) if downloaded is not None else None
    speed = max(0, float(speed)) if speed is not None else None
    progress = min(100, 100 * downloaded / size) if size and downloaded is not None else None
    eta = (
        max(0, size - downloaded) / speed
        if size and downloaded is not None and speed and state == "downloading"
        else None
    )
    return {
        "title": title(title_value),
        "state": state,
        "progress_percent": progress,
        "size_bytes": size,
        "downloaded_bytes": downloaded,
        "speed_bytes_per_second": speed,
        "eta_seconds": eta,
        "eta_estimated": eta is not None,
        "native_status_code": native,
    }


def paginate(fetch, key, maximum=1000):
    rows, total = [], None
    for offset in range(0, maximum, 100):
        data = fetch(offset)
        page, current = data[key], int(data["total"])
        if (
            not isinstance(page, list)
            or len(page) > 100
            or current < 0
            or (total is not None and current != total)
        ):
            raise ValueError("Inconsistent pagination")
        total = current
        rows.extend(page)
        if len(rows) >= total:
            if len(rows) != total:
                raise ValueError("Inconsistent total")
            break
        if len(page) != 100:
            raise ValueError("Incomplete page")
    return rows, total


def dsm_page(offset):
    # DSM's installed DownloadStation2 API is invoked as the named DSM user.
    # The old CGI API cannot be executed through synowebapi (error 102).
    result = subprocess.run(
        [
            "/usr/syno/bin/synowebapi",
            "--exec",
            "api=SYNO.DownloadStation2.Task",
            "version=2",
            "method=list",
            "runner=sbasadre",
            f"offset={offset}",
            "limit=100",
            'additional=["transfer"]',
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    if len(result.stdout) > MAX_BYTES:
        raise ValueError("DSM response too large")
    value = json.loads(result.stdout[result.stdout.find("{") :])
    if value.get("success") is not True:
        raise ValueError("DSM unavailable")
    return value["data"]


def collect_download_station(fetch=dsm_page):
    rows, total = paginate(fetch, "task")
    tasks = []
    for row in rows:
        native = row["status"]
        if type(native) is not int:
            raise ValueError("Invalid DSM status")
        state = "error" if native >= 101 else DS_STATES.get(native, "unknown")
        transfer = row["additional"]["transfer"]
        tasks.append(
            download(
                row["title"],
                state,
                row.get("size"),
                transfer.get("size_downloaded"),
                transfer.get("speed_download"),
                native,
            )
        )
    return validate_downloads(
        {
            "tasks": tasks,
            "total": total,
            "returned": len(tasks),
            "truncated": len(tasks) < total,
            "history": [],
            "history_total": 0,
            "history_truncated": False,
            "speed_bytes_per_second": sum(t["speed_bytes_per_second"] or 0 for t in tasks)
            if len(tasks) == total
            else None,
        }
    )


def sab_config():
    container = inspect_container("sabnzbd")
    if not container["State"]["Running"]:
        raise ValueError("SAB unavailable")
    mount = next(m["Source"] for m in container["Mounts"] if m["Destination"] == "/config")
    # ConfigObj has root-level keys and nested sections; read only scalar misc values.
    raw = read_private(Path(mount) / "sabnzbd.ini").decode()
    match = re.search(r"^\[misc\]\s*\n(.*?)(?=^\[|\Z)", raw, re.M | re.S)
    if not match:
        raise ValueError("SAB config unavailable")
    fields = {}
    for line in match[1].splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            if name.strip() in {"api_key", "port", "enable_https"}:
                fields[name.strip()] = value.strip().strip("\"'")
    key = fields.get("api_key", "")
    if not re.fullmatch(r"[a-zA-Z0-9]{16,128}", key):
        raise ValueError("SAB key unavailable")
    # SAB's configured port is its HTTP listener; HTTPS has a separate https_port.
    return tautulli_origin(container, fields.get("port", "8080"), False), key


def collect_sabnzbd():
    origin, key = sab_config()
    http = HTTP(timeout=10)
    queue_speed = None

    def page(mode, offset):
        nonlocal queue_speed
        if mode not in {"queue", "history"}:
            raise ValueError("SAB operation forbidden")
        data = http.request(
            origin + "/api",
            {"apikey": key, "mode": mode, "output": "json", "start": offset, "limit": 100},
        )[mode]
        if mode == "queue" and offset == 0 and "kbpersec" in data:
            queue_speed = float(data["kbpersec"]) * 1024
        return {"rows": data["slots"], "total": data["noofslots"]}

    queue, total = paginate(lambda offset: page("queue", offset), "rows")
    history, history_total = paginate(lambda offset: page("history", offset), "rows")
    tasks, completed = [], []
    for source, target in ((queue, tasks), (history, completed)):
        for row in source:
            state = SAB_STATES.get(row.get("status"), "unknown")
            size = float(row["mb"]) * 1024**2 if "mb" in row else row.get("bytes")
            downloaded = (
                max(0, size - float(row["mbleft"]) * 1024**2)
                if size is not None and "mbleft" in row
                else (size if state == "completed" else None)
            )
            # SAB does not reliably provide per-job instantaneous throughput.
            target.append(
                download(row.get("filename", row.get("name")), state, size, downloaded, None)
            )
    return validate_downloads(
        {
            "tasks": tasks,
            "total": total,
            "returned": len(tasks),
            "truncated": len(tasks) < total,
            "history": completed,
            "history_total": history_total,
            "history_truncated": len(completed) < history_total,
            "speed_bytes_per_second": queue_speed,
        }
    )


def build_state(collectors, previous=None):
    previous = validate_state(previous) if previous else None
    state = {
        "schema_version": 1,
        "generation": uuid.uuid4().hex,
        "generated_at": stamp(),
        "ttl_seconds": 300,
        "sources": {},
    }
    for source in SOURCES:
        row = (
            copy.deepcopy(previous["sources"][source])
            if previous
            else {"observed_at": None, "data": None, "scope": SCOPES[source]}
        )
        row.update(available=False, code="SOURCE_UNAVAILABLE")
        try:
            value = collectors[source]()
            (validate_library if source == "plex" else validate_downloads)(value)
            row.update(available=True, observed_at=stamp(), code=None, data=value)
        except Exception:
            pass
        state["sources"][source] = row
    state["generated_at"] = stamp()
    return validate_state(state)


def atomic_write(path, value, mode=0o640):
    raw = json.dumps(validate_state(value), allow_nan=False).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("Media projection too large")
    fd, temporary = tempfile.mkstemp(prefix=".media-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import fcntl

    with (args.output.parent / ".collector.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            previous = validate_state(json.loads(read_private(args.output)))
        except (OSError, ValueError):
            previous = None
        state = build_state(
            {
                "plex": lambda: Plex().collect(),
                "download_station": collect_download_station,
                "sabnzbd": collect_sabnzbd,
            },
            previous,
        )
        atomic_write(args.output, state)
        print(
            json.dumps(
                {
                    "generation": state["generation"],
                    "sources": {
                        s: {"available": row["available"], "observed_at": row["observed_at"]}
                        for s, row in state["sources"].items()
                    },
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('{"status":"COLLECTION_FAILED_DETAILS_SUPPRESSED"}')
        raise SystemExit(1) from None

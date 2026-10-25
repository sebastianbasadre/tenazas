"""No live requests: media semantics, source isolation and privacy regressions."""

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest
from tools import mcp_media_collect as collect
from tools.mcp_media_deploy import merge_media_source
from workspace.runtime import mcp_media_contract as contract
from workspace.runtime import mcp_media_reader as reader


def library():
    return {
        "libraries": [
            {"title": "Películas", "type": "movie", "movies": 12, "series": 0, "episodes": 0},
            {"title": "Series", "type": "show", "movies": 0, "series": 2, "episodes": 30},
        ],
        "totals": {"movies": 12, "series": 2, "episodes": 30},
        "recent": [],
        "recent_limit": 20,
        "recent_truncated": False,
        "active_playback": [],
        "excluded_library_types": {"artist": 1, "photo": 0, "other": 0},
        "counts_live_verified": True,
    }


def downloads():
    return {
        "tasks": [],
        "total": 0,
        "returned": 0,
        "truncated": False,
        "history": [],
        "history_total": 0,
        "history_truncated": False,
        "speed_bytes_per_second": 0,
    }


def state():
    return collect.build_state(
        {"plex": library, "download_station": downloads, "sabnzbd": downloads}
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://private/?apikey=secret",
        "magnet:?xt=urn:secret",
        "password=abc",
        "Bearer abcd",
        "token%253Dabc",
        "file/private",
        "name\\private",
        "A" * 40,
        "hello\nworld",
        "user@example.com",
        "https%253A%252F%252Fprivate",
    ],
)
def test_titles_never_publish_secrets_or_links(value):
    assert contract.title(value) == "[redacted]"


def test_unicode_titles_and_separate_movie_series_episode_counts():
    assert contract.title("El señor de los anillos (2001)") == "El señor de los anillos (2001)"
    assert contract.validate_library(library())["totals"] == {
        "movies": 12,
        "series": 2,
        "episodes": 30,
    }
    bad = library()
    bad["totals"]["movies"] += 1
    with pytest.raises(ValueError):
        contract.validate_library(bad)


@pytest.mark.parametrize("size,speed", [(None, 2), (0, 2), (100, 0), (100, None)])
def test_unknown_size_and_zero_speed_have_no_eta(size, speed):
    assert collect.download("Title", "downloading", size, 20, speed)["eta_seconds"] is None


def test_eta_is_explicit_estimate_and_completed_is_not_downloading():
    row = collect.download("Title", "downloading", 100, 20, 4)
    assert row["eta_seconds"] == 20 and row["eta_estimated"] is True
    assert collect.download("Title", "completed", 100, 100, 4)["eta_seconds"] is None


def test_dsm_installed_status_mapping_and_no_private_fields():
    def fetch(offset):
        return {
            "total": 1,
            "task": [
                {
                    "title": "Movie",
                    "status": 8,
                    "size": 100,
                    "username": "PRIVATE_USER",
                    "uri": "magnet:?PRIVATE",
                    "additional": {
                        "transfer": {"size_downloaded": 100, "speed_download": 0},
                        "detail": {"extract_password": "PRIVATE_PASSWORD"},
                    },
                }
            ],
        }

    result = collect.collect_download_station(fetch)
    assert result["tasks"][0]["state"] == "seeding"
    assert "PRIVATE" not in json.dumps(result)


def test_pagination_reads_all_pages_and_reports_hard_limit():
    calls = []

    def fetch(offset):
        calls.append(offset)
        return {"total": 201, "rows": list(range(offset, min(offset + 100, 201)))}

    rows, total = collect.paginate(fetch, "rows")
    assert len(rows) == total == 201 and calls == [0, 100, 200]
    rows, total = collect.paginate(fetch, "rows", maximum=100)
    assert len(rows) == 100 < total


def test_incomplete_page_is_unavailable_not_empty():
    with pytest.raises(ValueError):
        collect.paginate(lambda offset: {"total": 20, "rows": []}, "rows")


def test_failure_retains_last_observation_and_other_sources_advance():
    previous = state()

    def fail():
        raise RuntimeError("PRIVATE_PASSWORD")

    updated = collect.build_state(
        {"plex": fail, "download_station": downloads, "sabnzbd": downloads}, previous
    )
    row = updated["sources"]["plex"]
    assert row["available"] is False
    assert row["observed_at"] == previous["sources"]["plex"]["observed_at"]
    assert row["data"] == previous["sources"]["plex"]["data"]
    assert updated["sources"]["download_station"]["available"] is True
    assert "PRIVATE" not in json.dumps(updated)
    assert contract.view(updated, "plex")["snapshot"]["state"] == "stale"


def test_expiry_does_not_refresh_original_date():
    value = state()
    observed = value["sources"]["plex"]["observed_at"]
    result = contract.view(value, "plex", contract.utc(observed) + timedelta(seconds=301))
    assert result["snapshot"]["state"] == "stale"
    assert result["observed_at"] == observed


@pytest.mark.parametrize("source", contract.SOURCES)
def test_unknown_private_fields_are_rejected(source):
    value = state()
    value["sources"][source]["data"]["token"] = "PRIVATE"
    with pytest.raises(ValueError):
        contract.validate_state(value)


def test_reader_validates_projection_and_rejects_mutating_names(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state()))
    monkeypatch.setattr(reader, "CACHE_FILE", path)
    from workspace.runtime import mcp_cache_policy

    monkeypatch.setattr(
        mcp_cache_policy,
        "expected_identity",
        lambda: (
            (__import__("os").getuid(), __import__("os").getgid())
            if __import__("os").name == "posix"
            else None
        ),
    )
    path.chmod(0o600)
    assert reader.call("get_media_library")["data"]["totals"]["movies"] == 12
    assert reader.call("get_downloads")["sources"]["sabnzbd"]["data"]["tasks"] == []
    with pytest.raises(ValueError):
        reader.call("pause_download")


def test_missing_cache_is_unavailable_not_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(reader, "CACHE_FILE", tmp_path / "absent")
    result = reader.call("get_downloads")
    assert result["available"] is False and "tasks" not in result


def test_invalid_raw_title_is_rejected_on_vm():
    value = copy.deepcopy(state())
    value["sources"]["plex"]["data"]["libraries"][0]["title"] = "https://private/?token=secret"
    with pytest.raises(ValueError):
        contract.validate_state(value)


def test_sab_reads_only_queue_history_and_filters_private_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(collect, "sab_config", lambda: ("http://127.0.0.1:8080", "PRIVATE_KEY"))

    class FakeHTTP:
        def __init__(self, timeout):
            pass

        def request(self, url, form):
            calls.append(form.copy())
            assert url == "http://127.0.0.1:8080/api"
            assert set(form) == {"apikey", "mode", "output", "start", "limit"}
            if form["mode"] == "history":
                return {"history": {"slots": [], "noofslots": 0}}
            return {
                "queue": {
                    "slots": [
                        {
                            "filename": "Movie",
                            "status": "Downloading",
                            "mb": "100",
                            "mbleft": "20",
                            "storage": "/PRIVATE_PATH",
                            "url": "https://PRIVATE/?key=PRIVATE_KEY",
                        }
                    ],
                    "noofslots": 1,
                    "kbpersec": "10",
                }
            }

    monkeypatch.setattr(collect, "HTTP", FakeHTTP)
    result = collect.collect_sabnzbd()
    assert [c["mode"] for c in calls] == ["queue", "history"]
    assert result["tasks"][0]["progress_percent"] == 80
    assert result["tasks"][0]["speed_bytes_per_second"] is None
    assert result["speed_bytes_per_second"] == 10240
    assert "PRIVATE" not in json.dumps(result)


def test_media_deployment_preserves_existing_discovery_implementation():
    baseline = b"""ROUTES = ("/inventory", "/storage", "/network")
PRESERVED_DISCOVERY_IMPLEMENTATION = "existing-live-signal"
class SourceStore:
    pass
class SourceHandler:
    pass
def main():
    parser.add_argument("--filesystem", type=Path, required=True)
    parser.add_argument("--network-result-file", type=Path, required=True)
    store = SourceStore()
"""
    candidate = (
        Path(__file__).resolve().parents[1] / "workspace/runtime/mcp_sanitized_sources.py"
    ).read_bytes()
    result = merge_media_source(baseline, candidate).decode()
    assert 'PRESERVED_DISCOVERY_IMPLEMENTATION = "existing-live-signal"' in result
    assert 'parser.add_argument("--network-result-file"' in result
    assert 'parser.add_argument("--media-file"' in result
    assert 'MEDIA_ROUTE = "/media"' in result
    assert "read_state(self.media_file)" in result
    with pytest.raises(ValueError, match="boundary mismatch"):
        merge_media_source(
            baseline.replace(b"    store = SourceStore()", b"    store = other()"), candidate
        )

"""Mock-only source discovery; never contacts Plex, Tautulli or DSM."""

import io
import json
from urllib.parse import parse_qs

import pytest
from tools import mcp_media_probe as probe


def test_tautulli_summary_cannot_expose_raw_credentials_titles_or_users():
    identity = [{"machine_identifier": "PRIVATE_SERVER_IDENTIFIER", "token": "PRIVATE_TOKEN"}]
    libraries = [{"section_type": "movie", "section_name": "PRIVATE_LIBRARY", "count": "100"}]
    activity = {
        "sessions": [
            {
                "user": "PRIVATE_USER",
                "full_title": "PRIVATE_TITLE",
                "uri": "magnet:?PRIVATE_LINK",
                "token": "PRIVATE_TOKEN",
            }
        ]
    }
    result = probe.summarize_tautulli(identity, libraries, activity)
    assert result["status"] == "VERIFIED"
    assert result["active_sessions"] == 1
    assert result["libraries_by_type"]["movie"] == 1
    assert result["library_counts_live_verified"] is False
    assert "PRIVATE_" not in json.dumps(result)


def test_empty_activity_is_valid_but_missing_identity_is_not():
    result = probe.summarize_tautulli({"machine_identifier": "id"}, [], {"sessions": []})
    assert result["active_sessions"] == 0
    assert result["status"] == "VERIFIED"
    with pytest.raises(probe.ProbeError, match="PLEX_IDENTITY_UNAVAILABLE"):
        probe.summarize_tautulli([], [], {"sessions": []})


def test_tautulli_mutating_commands_are_rejected_before_transport():
    class NeverHTTP:
        def request(self, *args):
            pytest.fail("Forbidden operation reached transport")

    for command in ("terminate_session", "update", "refresh_libraries_list", "get_apikey", "sql"):
        with pytest.raises(probe.ProbeError, match="COMMAND_FORBIDDEN"):
            probe.tautulli_call(NeverHTTP(), "http://127.0.0.1/api/v2", "private-key", command)


def test_http_places_key_in_body_not_url_and_does_not_follow_redirects():
    requests = []

    class FakeOpener:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(b'{"response":{"result":"success","data":[]}}')

    http = probe.HTTP()
    http.opener = FakeOpener()
    probe.tautulli_call(http, "http://127.0.0.1/api/v2", "PRIVATE_KEY", "get_libraries")
    request = requests[0]
    assert request.method == "POST"
    assert "PRIVATE_KEY" not in request.full_url
    assert parse_qs(request.data.decode()) == {"apikey": ["PRIVATE_KEY"], "cmd": ["get_libraries"]}
    assert (
        probe.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com") is None
    )


def test_api_discovery_filters_other_apis_and_private_fields():
    result = probe.summarize_api_info(
        {
            "success": True,
            "sid": "PRIVATE_SESSION",
            "data": {
                "SYNO.DownloadStation.Task": {
                    "path": "DownloadStation/task.cgi",
                    "minVersion": 1,
                    "maxVersion": 3,
                    "token": "PRIVATE_TOKEN",
                },
                "SYNO.Secret.Admin": {"private": "PRIVATE_VALUE"},
            },
        }
    )
    assert result["status"] == "API_DISCOVERED"
    assert result["authenticated_task_read_verified"] is False
    assert set(result["apis"]) == {"SYNO.DownloadStation.Task"}
    assert "PRIVATE_" not in json.dumps(result)


@pytest.mark.parametrize("minimum,maximum", [(True, 3), (1, False), (3, 1), (1, 1001)])
def test_discovery_rejects_invalid_versions(minimum, maximum):
    with pytest.raises(probe.ProbeError, match="INVALID_API_VERSION"):
        probe.summarize_api_info(
            {
                "success": True,
                "data": {
                    "SYNO.DownloadStation.Task": {
                        "path": "entry.cgi",
                        "minVersion": minimum,
                        "maxVersion": maximum,
                    }
                },
            }
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://8.8.8.8",
        "http://127.0.0.1/?apikey=secret",
        "http://user:password@127.0.0.1",
        "file:///etc/passwd",
        "http://127.0.0.1/private",
    ],
)
def test_probe_rejects_external_or_credential_urls(url):
    with pytest.raises(probe.ProbeError, match="INVALID_LOCAL_URL"):
        probe.local_url(url)


def test_error_boundary_hides_transport_exception_details():
    def failed():
        raise RuntimeError("PRIVATE_PASSWORD https://private.local/?sid=PRIVATE_SESSION")

    result = probe.safe_probe(failed)
    assert result["status"] == "NOT_VERIFIED"
    assert "PRIVATE_" not in json.dumps(result)
    assert "checked_at" in result


def test_unpublished_tautulli_uses_only_its_inspected_private_address():
    container = {
        "HostConfig": {"NetworkMode": "media"},
        "NetworkSettings": {
            "Ports": {"8181/tcp": None},
            "Networks": {"media": {"IPAddress": "172.20.0.12"}},
        },
    }
    assert probe.tautulli_origin(container, "8181", False) == "http://172.20.0.12:8181"


def test_tautulli_prefers_loopback_published_port():
    container = {
        "HostConfig": {"NetworkMode": "media"},
        "NetworkSettings": {
            "Ports": {"8181/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18181"}]},
            "Networks": {"media": {"IPAddress": "172.20.0.12"}},
        },
    }
    assert probe.tautulli_origin(container, "8181", False) == "http://127.0.0.1:18181"


@pytest.mark.parametrize("addresses", [[], ["8.8.8.8"], ["172.20.0.12", "172.21.0.12"]])
def test_ambiguous_or_external_container_routes_are_not_used_for_credentials(addresses):
    container = {
        "HostConfig": {"NetworkMode": "media"},
        "NetworkSettings": {
            "Networks": {str(i): {"IPAddress": ip} for i, ip in enumerate(addresses)},
        },
    }
    with pytest.raises(probe.ProbeError, match="TAUTULLI_LOCAL_ROUTE_REQUIRES_REVIEW"):
        probe.tautulli_origin(container, "8181", False)


def test_tautulli_host_network_does_not_require_published_ports():
    assert probe.tautulli_origin({"HostConfig": {"NetworkMode": "host"}}, "8181", False) == (
        "http://127.0.0.1:8181"
    )


def test_shared_gluetun_namespace_uses_existing_publication_and_tautulli_key(monkeypatch):
    tautulli = {
        "State": {"Running": True},
        "HostConfig": {"NetworkMode": "container:gluetun"},
        "Mounts": [{"Destination": "/config", "Source": "/nas/tautulli"}],
        "NetworkSettings": {"Networks": {}, "Ports": {}},
    }
    gluetun = {
        "State": {"Running": True},
        "HostConfig": {"NetworkMode": "media"},
        "Mounts": [{"Destination": "/config", "Source": "/nas/vpn"}],
        "NetworkSettings": {"Ports": {"8181/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8181"}]}},
    }
    containers = {"tautulli": tautulli, "gluetun": gluetun}
    monkeypatch.setattr(probe, "inspect_container", containers.__getitem__)
    configs = []

    def read_config(path):
        configs.append(str(path).replace("\\", "/"))
        return "PRIVATE_TAUTULLI_KEY", "", "8181", False

    monkeypatch.setattr(probe, "read_config", read_config)
    requests = []
    responses = {
        "get_server_identity": [{"machine_identifier": "private-id"}],
        "get_libraries": [],
        "get_activity": {"sessions": []},
    }

    class MockHTTP:
        def request(self, url, form):
            requests.append((url, form))
            return {"response": {"result": "success", "data": responses[form["cmd"]]}}

    result = probe.probe_tautulli(MockHTTP())
    assert result["status"] == "VERIFIED"
    assert configs == ["/nas/tautulli/config.ini"]
    assert len(requests) == 3
    assert all(url == "http://127.0.0.1:8181/api/v2" for url, _ in requests)
    assert all(form["apikey"] == "PRIVATE_TAUTULLI_KEY" for _, form in requests)
    assert "PRIVATE_TAUTULLI_KEY" not in json.dumps(result)


def test_shared_network_cycle_is_rejected(monkeypatch):
    shared = {"State": {"Running": True}, "HostConfig": {"NetworkMode": "container:gluetun"}}
    monkeypatch.setattr(probe, "inspect_container", lambda name: shared)
    with pytest.raises(probe.ProbeError, match="CONTAINER_NETWORK_CYCLE"):
        probe.network_namespace_owner(shared)


def test_stopped_network_owner_is_rejected(monkeypatch):
    shared = {"HostConfig": {"NetworkMode": "container:gluetun"}}
    monkeypatch.setattr(probe, "inspect_container", lambda name: {"State": {"Running": False}})
    with pytest.raises(probe.ProbeError, match="NETWORK_OWNER_NOT_RUNNING"):
        probe.tautulli_origin(shared, "8181", False)


def test_http_configurable_timeout_reaches_transport():
    class FakeOpener:
        def open(self, request, timeout):
            assert timeout == 25
            return io.BytesIO(b'{}')

    http = probe.HTTP(timeout=25)
    http.opener = FakeOpener()
    assert http.request("http://127.0.0.1:8181") == {}


@pytest.mark.parametrize("timeout", [0, 61, True, 1.5])
def test_http_timeout_is_bounded(timeout):
    with pytest.raises(probe.ProbeError, match="INVALID_HTTP_TIMEOUT"):
        probe.HTTP(timeout=timeout)

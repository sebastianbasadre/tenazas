"""Keep unit tests from reaching live Ollama or the production inference proxy."""

import socket

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "live_inference: explicitly permits live Ollama integration checks")


@pytest.fixture(autouse=True)
def block_live_inference(request, monkeypatch):
    if request.node.get_closest_marker("live_inference"):
        return
    original = socket.socket.connect

    def guarded_connect(connection, address):
        if isinstance(address, tuple) and len(address) >= 2 and address[1] in (11434, 18902):
            raise OSError("Live inference is disabled in unit tests")
        return original(connection, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)

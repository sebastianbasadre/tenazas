import os

import pytest

from workspace.runtime.mcp_cache_policy import validate_cache
from workspace.runtime.mcp_cache_producer import export_cache


@pytest.fixture
def cache(tmp_path):
    root = tmp_path / "cache"
    export_cache({}, secret_values=[], destination=root)
    return root


def test_private_export_passes(cache):
    validate_cache(cache)


@pytest.mark.parametrize("name,mode", [("", 0o777), ("incidents", 0o755), ("state.json", 0o644), ("infra.json", 0o666)])
def test_open_permissions_fail_closed(cache, name, mode):
    (cache / name).chmod(mode)
    with pytest.raises(ValueError, match="permissions"):
        validate_cache(cache)


@pytest.mark.parametrize("name", ["extra.json", "incidents/foreign.json"])
def test_extra_files_fail_closed(cache, name):
    (cache / name).write_text('{}')
    with pytest.raises(ValueError, match="layout"):
        validate_cache(cache)


def test_symlink_fails_closed(cache):
    path = cache / "infra.json"
    path.unlink()
    path.symlink_to(cache / "state.json")
    with pytest.raises(ValueError):
        validate_cache(cache)


def test_hardlink_fails_closed(cache):
    path = cache / "infra.json"
    path.unlink()
    os.link(cache / "state.json", path)
    with pytest.raises(ValueError):
        validate_cache(cache)

import io
import time
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

import pytest

import iol_dependency as dependency
import iol_http


@pytest.fixture(autouse=True)
def temporary_status(tmp_path, monkeypatch):
    monkeypatch.setattr(dependency, "STATUS_FILE", tmp_path / "iol.json")


def test_auth_success_requires_a_portfolio_read():
    dependency.record_response("https://api.invertironline.com/token", 200)
    assert dependency.status()["degraded"]
    dependency.record_response("https://api.invertironline.com/api/v2/portafolio/argentina", 200)
    assert not dependency.status()["degraded"]
    dependency.record_response("https://api.invertironline.com/token", 403)
    assert dependency.status()["degraded"]


def test_old_live_data_is_degraded():
    from runtime_utils import write_atomic

    write_atomic(dependency.STATUS_FILE, {"degraded": False, "last_success_at": time.time() - 1000})
    assert dependency.status()["degraded"]


def test_auth_refresh_does_not_make_old_portfolio_fresh():
    from runtime_utils import write_atomic

    write_atomic(dependency.STATUS_FILE, {"degraded": False, "portfolio_ok": True,
                                        "portfolio_success_at": time.time() - 1000})
    dependency.record_response("https://api.invertironline.com/token", 200)
    assert dependency.status()["degraded"]


def test_http_error_is_compatible_and_marks_dependency_degraded():
    response = MagicMock(status_code=403, content=b"{}", headers={})
    with patch.object(iol_http.requests, "request", return_value=response):
        with pytest.raises(HTTPError) as error:
            iol_http.urlopen(Request("https://api.invertironline.com/token", data=b"grant_type=test"))
    assert error.value.code == 403
    assert isinstance(error.value.fp, io.BytesIO)
    assert dependency.status()["degraded"]

"""Standard HTTP transport for IOL, compatible with existing urllib callers."""

import io
from urllib.error import HTTPError, URLError

import requests

from iol_dependency import record_response


def urlopen(request, timeout=30):
    url = request.full_url
    headers = dict(request.header_items())
    headers.setdefault("User-Agent", "Tenazas/1.0")
    headers.setdefault("Accept", "application/json")
    try:
        response = requests.request(request.get_method(), url, data=request.data,
                                    headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        record_response(url, 503)
        raise URLError(type(exc).__name__) from None
    record_response(url, response.status_code)
    body = io.BytesIO(response.content)
    if response.status_code >= 400:
        raise HTTPError(url, response.status_code, "IOL HTTP error", response.headers, body)
    body.status = response.status_code
    body.headers = response.headers
    return body

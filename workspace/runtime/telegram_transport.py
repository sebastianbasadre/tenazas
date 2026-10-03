"""Telegram transport helpers extracted from router responsibilities."""

from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def telegram_request(token: str, method: str, data: dict | None = None, timeout: int = 35):
    body = urlencode(data or {}).encode("utf-8")
    request = Request(TELEGRAM_API.format(token=token, method=method), data=body)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def send_message(token: str, chat_id: str, text: str):
    return telegram_request(token, "sendMessage", {"chat_id": chat_id, "text": text})


def send_document(token: str, chat_id: str, path: str | Path, caption: str = ""):
    path = Path(path)
    boundary = f"----Tenazas{uuid.uuid4().hex}"
    chunks = []

    def field(name: str, value: str) -> None:
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode()
        )

    field("chat_id", chat_id)
    if caption:
        field("caption", caption)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    chunks.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
        f"filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode()
    )
    chunks.append(path.read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    request = Request(
        TELEGRAM_API.format(token=token, method="sendDocument"),
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))

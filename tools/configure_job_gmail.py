"""Interactive NAS/container Gmail setup. Never prints credentials.

Run with docker exec -it as tenazas. Uses TLS and an application password,
checks INBOX read-only, and saves a private tracker-only configuration.
Existing Himalaya configuration is never changed.
"""

import getpass
import imaplib
import json
import os
import re
import ssl
import sys
import tempfile
from pathlib import Path


def configuration(email, password):
    quote = json.dumps
    return f"""[accounts.jobs]
email = {quote(email)}
default = true
[accounts.jobs.backend]
type = "imap"
host = "imap.gmail.com"
port = 993
login = {quote(email)}
[accounts.jobs.backend.encryption]
type = "tls"
[accounts.jobs.backend.auth]
type = "password"
raw = {quote(password)}
"""


def verify(email, password):
    with imaplib.IMAP4_SSL(
        "imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=20
    ) as client:
        client.login(email, password)
        result, _ = client.select("INBOX", readonly=True)
        if result != "OK":
            raise ValueError("INBOX unavailable")


def save(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise ValueError("Unsafe credential path")
    descriptor, temporary = tempfile.mkstemp(prefix=".job-gmail-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if not sys.stdin.isatty():
        print("Ejecutá este asistente en una terminal interactiva (docker exec -it).")
        return 1
    email = input("Cuenta Gmail para seguimiento laboral: ").strip()
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
        print("Cuenta inválida.")
        return 1
    password = getpass.getpass("Contraseña de aplicación de Google (no se muestra): ").replace(
        " ", ""
    )
    if len(password) != 16 or not password.isalnum():
        print("Usá una contraseña de aplicación de Google de 16 caracteres.")
        return 1
    path = Path("/home/tenazas/.openclaw/state/assistant/job-gmail.toml")
    try:
        verify(email, password)
        save(path, configuration(email, password))
    except Exception:
        print(
            "No pude verificar Gmail. No guardé ni mostré credenciales nuevas. Revisá la cuenta y su contraseña de aplicación."
        )
        return 1
    print(
        "Gmail verificado con TLS. Configuración privada guardada. Usá /postulaciones correo en Telegram."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

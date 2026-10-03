"""Local document search with source/page citations; no credentials or global scans."""

import hashlib
import re
import sqlite3
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from assistant_jobs import ROOT

LIBRARY = ROOT / "assistant/documents"
INDEX = ROOT / "assistant/documents.sqlite"
ALLOWED = {".pdf", ".docx", ".txt", ".md", ".csv"}


def connect():
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(INDEX, timeout=10)
    connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(owner UNINDEXED, document UNINDEXED, filename UNINDEXED, page UNINDEXED, text, tokenize='unicode61 remove_diacritics 2')")
    return connection


def ingest(path, owner, filename=None):
    path = Path(path).resolve()
    folder = (LIBRARY / str(owner)).resolve()
    if not path.is_relative_to(folder) or path.is_symlink() or path.suffix.lower() not in ALLOWED:
        raise ValueError("Documento fuera de la biblioteca o formato no permitido")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Máximo 20 MB por documento")
    if path.suffix.lower() == ".pdf":
        result = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=40, check=True)
        pages = result.stdout.split("\f")
    elif path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 10 * 1024 * 1024:
                raise ValueError("Documento expandido demasiado grande")
            root = ElementTree.fromstring(archive.read(info))
            pages = [" ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))]
    else:
        pages = [path.read_text(encoding="utf-8", errors="replace")]
    document = hashlib.sha256(str(path).encode()).hexdigest()[:12]
    connection = connect()
    try:
        with connection:
            connection.execute("DELETE FROM pages WHERE owner=? AND document=?", (str(owner), document))
            for number, page in enumerate(pages, 1):
                if page.strip():
                    # Bounded chunks keep citations useful for long text-only files.
                    for start in range(0, min(len(page), 2_000_000), 3000):
                        reference = number if path.suffix.lower() == ".pdf" else start // 3000 + 1
                        connection.execute("INSERT INTO pages VALUES (?,?,?,?,?)", (str(owner), document, filename or path.name, reference, page[start:start + 3400]))
        count = connection.execute("SELECT count(*) FROM pages WHERE owner=? AND document=?", (str(owner), document)).fetchone()[0]
    finally:
        connection.close()
    if not count:
        raise ValueError("No encontré texto extraíble; el PDF puede necesitar OCR")
    return {"document": document, "filename": filename or path.name, "chunks": count}


def query(question, owner):
    words = re.findall(r"\w{3,}", question.lower())[:20]
    stop = {"como", "cómo", "para", "que", "qué", "del", "los", "las", "una", "con", "sobre", "documento"}
    words = [word for word in words if word not in stop]
    if not words:
        return "Indicá qué querés buscar en tus documentos."
    expression = " OR ".join('"' + word + '"' for word in words)
    connection = connect()
    try:
        rows = connection.execute("SELECT filename,page,snippet(pages,4,'[',']',' … ',70) FROM pages WHERE pages MATCH ? AND owner=? ORDER BY bm25(pages) LIMIT 4", (expression, str(owner))).fetchall()
    finally:
        connection.close()
    if not rows:
        return "No encontré evidencia en tus documentos. Adjuntá el archivo con la leyenda /documento."
    return "Fragmentos de tus documentos:\n\n" + "\n\n".join(f"[{index}] {name} · {'página' if name.lower().endswith('.pdf') else 'fragmento'} {page}\n{text}" for index, (name, page, text) in enumerate(rows, 1))


def list_documents(owner):
    connection = connect()
    try:
        return connection.execute("SELECT document,filename,count(*) FROM pages WHERE owner=? GROUP BY document,filename LIMIT 30", (str(owner),)).fetchall()
    finally:
        connection.close()


def delete(document, owner):
    connection = connect()
    try:
        with connection:
            changed = connection.execute("DELETE FROM pages WHERE owner=? AND document=?", (str(owner), document)).rowcount
    finally:
        connection.close()
    for path in (LIBRARY / str(owner)).glob("*"):
        if hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12] == document:
            path.unlink()
    return bool(changed)

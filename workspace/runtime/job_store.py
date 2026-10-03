"""Durable job records and atomic claims for the authenticated runner."""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class JobStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, job TEXT NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL, lease_until REAL,
            attempts INTEGER NOT NULL DEFAULT 0, result TEXT)""")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def claim(self, job, key, timeout):
        now = time.time()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Never retry side effects blindly after a crash: require operator review.
            connection.execute(
                """UPDATE jobs SET status='interrupted', updated=?
                WHERE status='running' AND lease_until < ?""",
                (now, now),
            )
            existing = connection.execute("SELECT * FROM jobs WHERE id=?", (key,)).fetchone()
            if existing:
                if existing["job"] != job:
                    return {
                        "ok": False,
                        "error": "idempotency key belongs to another job",
                        "conflict": True,
                    }
                if existing["result"]:
                    return {**json.loads(existing["result"]), "deduplicated": True}
                return {"ok": False, "job": job, "error": existing["status"], "conflict": True}
            busy = connection.execute(
                """SELECT id FROM jobs
                WHERE job=? AND status IN ('running', 'interrupted') LIMIT 1""",
                (job,),
            ).fetchone()
            if busy:
                return {
                    "ok": False,
                    "job": job,
                    "error": "job running or awaiting crash review",
                    "conflict": True,
                }
            connection.execute(
                """INSERT INTO jobs
                (id,job,status,created,updated,lease_until,attempts) VALUES (?,?,'running',?,?,?,1)""",
                (key, job, now, now, now + timeout + 30),
            )
        return None

    def finish(self, key, result):
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, updated=?, lease_until=NULL, result=? WHERE id=?",
                (
                    "succeeded" if result.get("ok") else "failed",
                    time.time(),
                    json.dumps(result),
                    key,
                ),
            )

    def summary(self):
        with self.connect() as connection:
            counts = dict(
                connection.execute("SELECT status, count(*) FROM jobs GROUP BY status").fetchall()
            )
            last = connection.execute(
                "SELECT max(updated) FROM jobs WHERE status='succeeded'"
            ).fetchone()[0]
        return {"counts": counts, "last_success_ts": last or 0}

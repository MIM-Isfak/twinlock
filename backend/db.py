import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from threading import Lock
from typing import Any

from backend import config
from wizard.validator import ManifestError, validate_manifest

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    user_id TEXT NOT NULL,
    device_id TEXT NOT NULL UNIQUE,
    manifest TEXT NOT NULL,
    PRIMARY KEY (user_id, device_id)
);
CREATE TABLE IF NOT EXISTS leases (
    user_id TEXT NOT NULL,
    lease_id TEXT NOT NULL PRIMARY KEY,
    device_id TEXT NOT NULL,
    action TEXT NOT NULL,
    params TEXT NOT NULL,
    state_version TEXT NOT NULL,
    precondition_values TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'APPROVED', 'CONSUMED', 'EXPIRED', 'REVOKED', 'INVALIDATED'
    )),
    expires_at REAL NOT NULL,
    UNIQUE (user_id, lease_id),
    FOREIGN KEY (user_id, device_id) REFERENCES devices (user_id, device_id)
);
CREATE INDEX IF NOT EXISTS leases_by_user ON leases (user_id, status, expires_at);
CREATE TABLE IF NOT EXISTS run_ledger (
    user_id TEXT NOT NULL,
    lease_id TEXT NOT NULL PRIMARY KEY,
    device_id TEXT NOT NULL,
    duration_s TEXT NOT NULL,
    executed_at REAL NOT NULL,
    FOREIGN KEY (user_id, lease_id) REFERENCES leases (user_id, lease_id),
    FOREIGN KEY (user_id, device_id) REFERENCES devices (user_id, device_id)
);
CREATE INDEX IF NOT EXISTS runs_by_device ON run_ledger (user_id, device_id, executed_at);
CREATE TABLE IF NOT EXISTS audit_log (
    event_id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    event TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT,
    lease_id TEXT,
    occurred_at REAL NOT NULL,
    FOREIGN KEY (user_id, lease_id) REFERENCES leases (user_id, lease_id)
);
CREATE INDEX IF NOT EXISTS audit_by_user ON audit_log (user_id, event, occurred_at);
"""


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._lock = Lock()
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def register_device(self, manifest: Mapping[str, Any]) -> None:
        device = validate_manifest(manifest)
        with self.transaction() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM devices WHERE user_id = ?", (device["owner"],)
            ).fetchone()[0]
            if count >= config.DEVICES_PER_USER:
                raise ManifestError("device_quota_exceeded")
            if conn.execute(
                "SELECT 1 FROM devices WHERE device_id = ?", (device["id"],)
            ).fetchone():
                raise ManifestError("device_already_registered")
            conn.execute(
                "INSERT INTO devices (user_id, device_id, manifest) VALUES (?, ?, ?)",
                (device["owner"], device["id"], json.dumps(device, allow_nan=False)),
            )


def get_device(conn: sqlite3.Connection, user_id: str, device_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT manifest FROM devices WHERE user_id = ? AND device_id = ?", (user_id, device_id)
    ).fetchone()
    return json.loads(row["manifest"]) if row else None


def recent_proposals(conn: sqlite3.Connection, user_id: str, now: float) -> int:
    return conn.execute(
        """SELECT COUNT(*) FROM audit_log
           WHERE user_id = ? AND event = 'propose' AND occurred_at > ?""",
        (user_id, now - config.PROPOSAL_WINDOW_S),
    ).fetchone()[0]


def used_duration(
    conn: sqlite3.Connection, user_id: str, device_id: str, since: float
) -> Fraction:
    rows = conn.execute(
        """SELECT duration_s FROM run_ledger
           WHERE user_id = ? AND device_id = ? AND executed_at > ?""",
        (user_id, device_id, since),
    )
    return sum((Fraction(row["duration_s"]) for row in rows), Fraction())


def record_run(
    conn: sqlite3.Connection, user_id: str, lease_id: str, device_id: str,
    duration_s: int | float, now: float,
) -> None:
    conn.execute(
        """INSERT INTO run_ledger (user_id, lease_id, device_id, duration_s, executed_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, lease_id, device_id, str(duration_s), now),
    )


def audit(
    conn: sqlite3.Connection, user_id: str, event: str, outcome: str, now: float,
    *, reason: str | None = None, lease_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO audit_log (user_id, event, outcome, reason, lease_id, occurred_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, event, outcome, reason, lease_id, now),
    )

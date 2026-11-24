import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Lease:
    user_id: str
    device_id: str
    action: str
    params: dict[str, Any]
    state_version: int
    precondition_values: list[dict[str, Any]]
    status: str


def create_lease(
    conn: sqlite3.Connection, lease: Lease, expires_at: float,
) -> str:
    lease_id = uuid4().hex
    conn.execute(
        """INSERT INTO leases (user_id, lease_id, device_id, action, params, state_version,
                               precondition_values, status, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (lease.user_id, lease_id, lease.device_id, lease.action, json.dumps(lease.params),
         str(lease.state_version), json.dumps(lease.precondition_values), lease.status, expires_at),
    )
    return lease_id


def expire_lease(conn: sqlite3.Connection, user_id: str, lease_id: str, now: float) -> None:
    conn.execute(
        """UPDATE leases SET status = 'EXPIRED'
           WHERE user_id = ? AND lease_id = ? AND status IN ('PENDING', 'APPROVED')
             AND expires_at <= ?""",
        (user_id, lease_id, now),
    )


def get_lease(conn: sqlite3.Connection, user_id: str, lease_id: str) -> Lease | None:
    row = conn.execute(
        "SELECT * FROM leases WHERE user_id = ? AND lease_id = ?", (user_id, lease_id)
    ).fetchone()
    if row is None:
        return None
    return Lease(
        user_id=row["user_id"], device_id=row["device_id"], action=row["action"],
        params=json.loads(row["params"]), state_version=int(row["state_version"]),
        precondition_values=json.loads(row["precondition_values"]), status=row["status"],
    )


def count_pending(conn: sqlite3.Connection, user_id: str, now: float) -> int:
    return conn.execute(
        """SELECT COUNT(*) FROM leases JOIN devices USING (user_id, device_id)
           WHERE user_id = ? AND status = 'PENDING' AND expires_at > ?
             AND json_extract(manifest, '$.approval.required') = 1""",
        (user_id, now),
    ).fetchone()[0]


def invalidate_lease(
    conn: sqlite3.Connection, user_id: str, lease_id: str, now: float,
    *, prior_status: str = "APPROVED",
) -> None:
    conn.execute(
        """UPDATE leases SET status = 'INVALIDATED'
           WHERE user_id = ? AND lease_id = ? AND status = ? AND expires_at > ?""",
        (user_id, lease_id, prior_status, now),
    )


def consume_lease(
    conn: sqlite3.Connection, user_id: str, lease_id: str, now: float,
    *, prior_status: str = "APPROVED",
) -> bool:
    return conn.execute(
        """UPDATE leases SET status = 'CONSUMED'
           WHERE user_id = ? AND lease_id = ? AND status = ? AND expires_at > ?""",
        (user_id, lease_id, prior_status, now),
    ).rowcount == 1


def redeem_command(
    conn: sqlite3.Connection, lease_id: str, device_id: str, action: str,
    params: Mapping[str, object], now: float,
) -> float | None:
    row = conn.execute(
        """SELECT params, expires_at FROM leases
           WHERE lease_id = ? AND device_id = ? AND action = ?
             AND status = 'CONSUMED' AND expires_at > ?""",
        (lease_id, device_id, action, now),
    ).fetchone()
    if row is None or json.loads(row["params"]) != params:
        return None
    if not conn.execute(
        "INSERT OR IGNORE INTO controller_receipts (lease_id) VALUES (?)", (lease_id,),
    ).rowcount:
        return None
    return row["expires_at"]

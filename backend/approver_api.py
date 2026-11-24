"""Trusted approval channel. Keep this module out of agent tool wiring."""

from collections.abc import Callable
from dataclasses import dataclass
from time import time

from backend.db import Database, audit, get_device
from backend.lease import expire_lease


@dataclass(frozen=True)
class PendingLease:
    lease_id: str
    device_id: str
    action: str
    params: str
    expires_at: float
    seconds_remaining: float


class ApproverAPI:
    def __init__(self, db: Database, clock: Callable[[], float] = time) -> None:
        self._db = db
        self._clock = clock

    def list_pending(self, user_id: str) -> list[PendingLease]:
        with self._db.transaction() as conn:
            now = self._clock()
            rows = conn.execute(
                """SELECT lease_id, device_id, action, params, expires_at
                   FROM leases JOIN devices USING (user_id, device_id)
                   WHERE user_id = ? AND status = 'PENDING' AND expires_at > ?
                     AND json_extract(manifest, '$.approval.required') = 1
                   ORDER BY expires_at, lease_id""", (user_id, now),
            )
            return [PendingLease(*row, seconds_remaining=row["expires_at"] - now) for row in rows]

    def approve(self, lease_id: str, *, user_id: str | None = None) -> bool:
        return self._transition(lease_id, "approve", user_id)

    def revoke(self, lease_id: str, *, user_id: str | None = None) -> bool:
        return self._transition(lease_id, "revoke", user_id)

    def deny(self, lease_id: str, *, user_id: str) -> bool:
        return self._transition(lease_id, "deny", user_id)

    def _transition(self, lease_id: str, event: str, owner: str | None) -> bool:
        if not isinstance(lease_id, str) or not lease_id.isascii():
            return False
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT user_id, device_id FROM leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
            if row is None or (owner is not None and owner != row["user_id"]):
                return False
            user_id = row["user_id"]
            device = get_device(conn, user_id, row["device_id"])
            if device is None or not device["approval"]["required"]:
                return False
            now = self._clock()
            expire_lease(conn, user_id, lease_id, now)
            if event == "approve":
                status, prior = "APPROVED", ("PENDING",)
            elif event == "revoke":
                status, prior = "REVOKED", ("PENDING", "APPROVED")
            else:
                status, prior = "REVOKED", ("PENDING",)
            placeholders = ", ".join("?" for _ in prior)
            changed = conn.execute(
                f"""UPDATE leases SET status = ?
                    WHERE user_id = ? AND lease_id = ? AND expires_at > ?
                      AND status IN ({placeholders})""",
                (status, user_id, lease_id, now, *prior),
            ).rowcount == 1
            audit(conn, user_id, event, status.lower() if changed else "refused", now,
                  lease_id=lease_id)
            return changed

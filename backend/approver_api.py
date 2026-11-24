"""Trusted approval channel. Keep this module out of agent tool wiring."""

from collections.abc import Callable
from time import time

from backend.db import Database, audit
from backend.lease import expire_lease


class ApproverAPI:
    def __init__(self, db: Database, clock: Callable[[], float] = time) -> None:
        self._db = db
        self._clock = clock

    def approve(self, lease_id: str) -> bool:
        return self._transition(lease_id, "approve")

    def revoke(self, lease_id: str) -> bool:
        return self._transition(lease_id, "revoke")

    def _transition(self, lease_id: str, event: str) -> bool:
        if not isinstance(lease_id, str) or not lease_id.isascii():
            return False
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT user_id FROM leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
            if row is None:
                return False
            user_id = row["user_id"]
            now = self._clock()
            expire_lease(conn, user_id, lease_id, now)
            if event == "approve":
                status, prior = "APPROVED", ("PENDING",)
            else:
                status, prior = "REVOKED", ("PENDING", "APPROVED")
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

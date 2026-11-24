import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from sqlite3 import Connection
from time import time
from typing import Any

from backend import db, lease, policy
from backend.policy import Decision
from backend.state import StateProvider
from wizard.validator import is_identifier, is_number


@dataclass(frozen=True)
class Result:
    status: str
    reason: str | None = None
    lease_id: str | None = None
    blocked_by: str | None = None


class Engine:
    def __init__(
        self, store: db.Database, state_provider: StateProvider, clock: Callable[[], float] = time,
    ) -> None:
        self._store = store
        self._state = state_provider
        self._clock = clock

    def propose(
        self, user_id: str, device_id: str, action: str, params: Mapping[str, object],
    ) -> Result:
        if isinstance(params, Mapping):
            params = dict(params)
        with self._store.transaction() as conn:
            now = self._clock()
            if not self._store.enable_backend_policy:
                return self._propose_without_policy(conn, user_id, device_id, action, params, now)
            recent = db.recent_proposals(conn, user_id, now)
            rate = policy.check_proposal_rate(recent)
            if rate != policy.ALLOWED:
                return _report(conn, user_id, "proposal_rate_limited", now, rate)

            device = db.get_device(conn, user_id, device_id) if is_identifier(device_id) else None
            action_check = policy.check_action(user_id, device, action, params)
            if device is None or action_check != policy.ALLOWED:
                return _report(conn, user_id, "propose", now, action_check)

            approval = device["approval"]
            if approval["required"]:
                quota = policy.check_pending_quota(lease.count_pending(conn, user_id, now))
                if quota != policy.ALLOWED:
                    return _report(conn, user_id, "propose", now, quota)

            state = self._state.get_state(device_id)
            checked_at = self._clock()
            state_check, evidence = policy.check_preconditions(
                device["preconditions"], state, checked_at,
            )
            if state is None or state_check != policy.ALLOWED:
                return _report(conn, user_id, "propose", now, state_check)
            budget = _check_budget(conn, user_id, device, params, checked_at)
            if budget != policy.ALLOWED:
                return _report(conn, user_id, "propose", now, budget)

            required = approval["required"]
            # Approval-free leases can be consumed from PENDING under the registered policy.
            lease_id = lease.create_lease(
                conn,
                lease.Lease(user_id, device_id, action, dict(params), state.version, evidence,
                            "PENDING"),
                checked_at + approval["ttl_s"],
            )
            outcome = Decision("pending_approval" if required else "approved")
            return _report(conn, user_id, "propose", now, outcome, lease_id)

    def _propose_without_policy(
        self, conn: Connection, user_id: str, device_id: str, action: str,
        params: Mapping[str, object], now: float,
    ) -> Result:
        device = db.get_device(conn, user_id, device_id) if is_identifier(device_id) else None
        if device is None:
            return _report(conn, user_id, "propose", now, Decision("refused", "device_not_found"))
        if not _can_store_action(action, params):
            return _report(conn, user_id, "propose", now, Decision("refused", "invalid_tool_payload"))
        state = self._state.get_state(device_id)
        lease_id = lease.create_lease(
            conn, lease.Lease(user_id, device_id, action, dict(params),
                              state.version if state is not None else 0, [], "PENDING"),
            now + device["approval"]["ttl_s"],
        )
        return _report(conn, user_id, "propose", now, Decision("approved"), lease_id)

    def execute(self, user_id: str, lease_id: str) -> Result:
        with self._store.transaction() as conn:
            now = self._clock()
            if not isinstance(lease_id, str) or not lease_id.isascii():
                return _report(conn, user_id, "execute", now, Decision("refused", "lease_not_found"))
            lease.expire_lease(conn, user_id, lease_id, now)
            bound = lease.get_lease(conn, user_id, lease_id)
            if bound is None:
                return _report(conn, user_id, "execute", now, Decision("refused", "lease_not_found"))
            device = db.get_device(conn, user_id, bound.device_id)
            needs_approval = self._store.enable_backend_policy and (
                device is None or device["approval"]["required"]
            )
            if bound.status == "PENDING" and needs_approval:
                outcome = Decision("pending_approval")
            elif bound.status in {"APPROVED", "PENDING"}:
                outcome, now = self._execute_authorized(conn, lease_id, bound, device)
            else:
                outcome = Decision("refused", f"lease_{bound.status.lower()}")
            return _report(conn, user_id, "execute", now, outcome, lease_id)

    def _execute_authorized(
        self, conn: Connection, lease_id: str, bound: lease.Lease, device: dict[str, Any] | None,
    ) -> tuple[Decision, float]:
        if not self._store.enable_backend_policy:
            return self._consume(conn, lease_id, bound)
        action = policy.check_action(bound.user_id, device, bound.action, bound.params)
        if device is None or action != policy.ALLOWED:
            return self._invalidate(conn, lease_id, bound, action)

        state = self._state.get_state(bound.device_id)
        budget = _check_budget(conn, bound.user_id, device, bound.params, self._clock())
        now = self._clock()
        check, evidence = policy.check_preconditions(device["preconditions"], state, now)
        if check != policy.ALLOWED:
            return self._invalidate(conn, lease_id, bound, check)
        if state is None or state.version != bound.state_version or evidence != bound.precondition_values:
            return self._invalidate(
                conn, lease_id, bound, Decision("refused", "state_changed"),
            )

        if budget != policy.ALLOWED:
            return budget, now
        return self._consume(conn, lease_id, bound)

    def _consume(self, conn: Connection, lease_id: str, bound: lease.Lease) -> tuple[Decision, float]:
        now = self._clock()
        if not lease.consume_lease(conn, bound.user_id, lease_id, now, prior_status=bound.status):
            lease.expire_lease(conn, bound.user_id, lease_id, now)
            return Decision("refused", "lease_expired"), now
        duration = bound.params.get("duration_s", 0)
        if is_number(duration) and duration >= 0:
            db.record_run(conn, bound.user_id, lease_id, bound.device_id, duration, now)
        return Decision("executed"), now

    def _invalidate(
        self, conn: Connection, lease_id: str, bound: lease.Lease, outcome: Decision,
    ) -> tuple[Decision, float]:
        now = self._clock()
        lease.expire_lease(conn, bound.user_id, lease_id, now)
        lease.invalidate_lease(conn, bound.user_id, lease_id, now, prior_status=bound.status)
        return outcome, now


def _can_store_action(action: str, params: Mapping[str, object]) -> bool:
    if not isinstance(action, str) or not isinstance(params, Mapping):
        return False
    try:
        action.encode("utf-8")
        json.dumps(dict(params), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return False
    return True


def _check_budget(
    conn: Connection, user_id: str, device: Mapping[str, Any],
    params: Mapping[str, object], now: float,
) -> Decision:
    budget = device["budget"]
    used = Fraction()
    if budget is not None:
        used = db.used_duration(conn, user_id, device["id"], now - budget["window_s"])
    return policy.check_budget(budget, params, used)


def _report(
    conn: Connection, user_id: str, event: str, now: float, outcome: Decision,
    lease_id: str | None = None,
) -> Result:
    db.audit(conn, user_id, event, outcome.status, now, reason=outcome.reason, lease_id=lease_id)
    return Result(outcome.status, outcome.reason, lease_id)

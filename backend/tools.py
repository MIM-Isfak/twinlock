from collections.abc import Callable, Mapping
from time import time

from backend import db
from backend.engine import Engine, Result
from backend.lease import get_lease
from backend.state import StateProvider
from wizard.validator import is_identifier

Actuate = Callable[[str, str, Mapping[str, object], str], str | None]
TOOL_ARGUMENTS = {
    "list_devices": (),
    "get_device_state": ("device_id",),
    "propose_action": ("device_id", "action", "params"),
    "execute_action": ("lease_id",),
}
TOOL_NAMES = tuple(TOOL_ARGUMENTS)


class ToolHandlers:
    def __init__(
        self, caller: str, store: db.Database, engine: Engine, state: StateProvider,
        actuate: Actuate | None,
    ) -> None:
        self._caller = caller
        self._store = store
        self._engine = engine
        self._state = state
        self._actuate = actuate

    def list_devices(self) -> list[dict[str, str]]:
        with self._store.transaction() as conn:
            devices = db.list_devices(conn, self._caller)
        return [{key: device[key] for key in ("id", "type", "risk")} for device in devices]

    def get_device_state(self, device_id: str) -> dict[str, object]:
        with self._store.transaction() as conn:
            if not is_identifier(device_id) or db.get_device(conn, self._caller, device_id) is None:
                return {"status": "refused", "reason": "device_not_found"}
        state = self._state.get_state(device_id)
        if state is None:
            return {"status": "inconclusive", "reason": "state_missing"}
        return {"state": dict(state.values), "timestamp": state.timestamp,
                "state_version": state.version}

    def propose_action(self, device_id: str, action: str, params: Mapping[str, object]) -> Result:
        return self._engine.propose(self._caller, device_id, action, params)

    def execute_action(self, lease_id: str) -> Result:
        result = self._engine.execute(self._caller, lease_id)
        if result.status != "executed" or self._actuate is None:
            return result
        with self._store.transaction() as conn:
            bound = get_lease(conn, self._caller, lease_id)
        if bound is None:
            return Result("refused", "lease_not_found")
        reason = self._actuate(bound.device_id, bound.action, bound.params, lease_id)
        if reason is None:
            return result
        with self._store.transaction() as conn:
            db.audit(conn, self._caller, "controller", "refused", time(),
                     reason=reason, lease_id=lease_id)
        return Result("refused", reason, lease_id, blocked_by="L4")

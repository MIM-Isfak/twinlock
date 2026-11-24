import argparse
from collections.abc import Callable, Mapping
from dataclasses import asdict
import json
from time import monotonic, sleep, time
from typing import Any
from uuid import uuid4

from backend import config, db, lease
from backend.demo import register_demo_devices
from backend.engine import Engine, Result
from backend.state import StateProvider
from backend.tools import ToolHandlers
from simulator.controller import Controller

DEMO_TARGETS = {"user1": "pump-1", "user2": "hall-light"}
ACTIONS = {"pump": "run", "door_lock": "unlock", "switch": "on"}
TOOL_NAMES = ("list_devices", "get_device_state", "propose_action", "execute_action")


class MockRunner:
    def __init__(
        self, store: db.Database, *, clock: Callable[[], float] = time,
        controller_clock: Callable[[], float] = monotonic,
        enable_controller: bool | None = None, state_provider: StateProvider | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._sessions: dict[str, str] = {}
        register_demo_devices(store)
        with store.transaction() as conn:
            device_types = {
                device["id"]: device["type"]
                for user in DEMO_TARGETS for device in db.list_devices(conn, user)
            }
        self.controller = Controller(device_types, self._verify_token, clock=controller_clock,
                                     wall_clock=clock)
        enabled = config.enable_controller if enable_controller is None else enable_controller
        self._actuate = self.controller.command if enabled else None
        self._state = self.controller if state_provider is None else state_provider
        self._engine = Engine(store, self._state, clock)

    def open_session(self, user: str) -> str:
        if user not in DEMO_TARGETS:
            raise ValueError("unknown_demo_user")
        session = uuid4().hex
        self._sessions[session] = user
        return session

    def call(self, session: str, tool: str, **params: object) -> Any:
        if tool not in TOOL_NAMES:
            raise ValueError("unknown_tool")
        handlers = ToolHandlers(self._sessions[session], self._store, self._engine,
                                self._state, self._actuate)
        return getattr(handlers, tool)(**params)

    def _verify_token(
        self, token: str, device_id: str, action: str, params: Mapping[str, object],
    ) -> float | None:
        with self._store.transaction() as conn:
            return lease.redeem_command(conn, token, device_id, action, params, self._clock())


def run_script(runner: MockRunner, user: str, device_id: str) -> int:
    session = runner.open_session(user)
    devices = runner.call(session, "list_devices")
    print(json.dumps(devices))
    device = next((entry for entry in devices if entry["id"] == device_id), None)
    if device is None or device["type"] not in ACTIONS:
        print("No supported simulator device matches the requested ID.")
        return 1
    print(json.dumps(runner.call(session, "get_device_state", device_id=device_id)))
    action = ACTIONS[device["type"]]
    params = {"duration_s": config.MOCK_ACTION_DURATION_S} if action != "on" else {}
    proposal: Result = runner.call(session, "propose_action", device_id=device_id,
                                   action=action, params=params)
    print(json.dumps(asdict(proposal)), flush=True)
    if proposal.lease_id is None:
        return 1
    if proposal.status == "pending_approval":
        print(f"Review in another terminal with approver.approve and --user {user}. "
              "Use the same database path.")
        try:
            input("Press Enter after the human decision. ")
        except (EOFError, KeyboardInterrupt):
            return 1

    runner.controller.heartbeat()
    result: Result = runner.call(session, "execute_action", lease_id=proposal.lease_id)
    print(json.dumps(asdict(result)))
    if result.status != "executed":
        return 1
    while True:
        snapshot = runner.controller.get_state(device_id)
        if snapshot is None:
            return 1
        values = snapshot.values
        if not values.get("running") and values.get("locked", True):
            print(json.dumps(runner.call(session, "get_device_state", device_id=device_id)))
            return 0
        sleep(config.CONTROLLER_POLL_INTERVAL_S)
        runner.controller.heartbeat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline TwinLock scripted agent.")
    parser.add_argument("--db", default=config.MOCK_DB_PATH)
    parser.add_argument("--user", required=True, choices=DEMO_TARGETS)
    parser.add_argument("--device-id")
    args = parser.parse_args(argv)
    store = db.Database(args.db)
    try:
        runner = MockRunner(store)
        with runner.controller:
            return run_script(runner, args.user, args.device_id or DEMO_TARGETS[args.user])
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())

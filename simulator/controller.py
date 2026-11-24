from collections.abc import Callable, Mapping
from threading import Event, Lock, Thread
from time import monotonic, time

from backend import config
from backend.state import DeviceState, StateProvider
from simulator.adapters.door_lock import DoorLockAdapter
from simulator.adapters.pump import PumpAdapter
from simulator.adapters.switch import SwitchAdapter
from simulator.tank import Tank
from wizard.validator import is_number

VerifyToken = Callable[[str, str, str, Mapping[str, object]], float | None]


class Controller(StateProvider):
    def __init__(
        self, device_types: Mapping[str, str], verify_token: VerifyToken,
        *, clock: Callable[[], float] = monotonic, wall_clock: Callable[[], float] = time,
    ) -> None:
        self._verify_token = verify_token
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = Lock()
        self._last_update = clock()
        self._heartbeat_deadline = float("-inf")
        self._used_tokens: dict[str, float] = {}
        self._pumps = {
            device_id: PumpAdapter(Tank(device_id))
            for device_id, kind in device_types.items() if kind == "pump"
        }
        self._doors = {
            device_id: DoorLockAdapter()
            for device_id, kind in device_types.items() if kind == "door_lock"
        }
        self._switches = {
            device_id: SwitchAdapter()
            for device_id, kind in device_types.items() if kind == "switch"
        }
        self._shutdown = Event()
        self._worker = Thread(target=self._poll, daemon=True)

    def __enter__(self) -> "Controller":
        self._worker.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._shutdown.set()
        self._worker.join()
        with self._lock:
            now = self._advance()
            self._heartbeat_deadline = now
            self._advance()

    def _poll(self) -> None:
        while not self._shutdown.wait(config.CONTROLLER_POLL_INTERVAL_S):
            with self._lock:
                self._advance()

    def heartbeat(self) -> None:
        with self._lock:
            now = self._advance()
            self._heartbeat_deadline = now + config.CONTROLLER_HEARTBEAT_TIMEOUT_S

    def set_tank_level(self, device_id: str, level_pct: float) -> None:
        with self._lock:
            self._advance()
            self._pumps[device_id].tank.set_level(level_pct)
            self._advance()

    def get_state(self, device_id: str) -> DeviceState | None:
        with self._lock:
            self._advance()
            timestamp = self._wall_clock()
            if device_id in self._pumps:
                return self._pumps[device_id].tank.snapshot(timestamp)
            adapter = self._doors.get(device_id) or self._switches.get(device_id)
            return adapter.snapshot(timestamp) if adapter is not None else None

    def command(
        self, device_id: str, action: str, params: Mapping[str, object],
        lease_token: str | None = None,
    ) -> str | None:
        if not isinstance(lease_token, str) or not lease_token or not lease_token.isascii():
            return "invalid_lease_token"
        params = dict(params)
        expires_at = self._verify_token(lease_token, device_id, action, params)
        with self._lock:
            now = self._advance()
            wall_now = self._wall_clock()
            self._used_tokens = {
                token: expiry for token, expiry in self._used_tokens.items() if expiry > wall_now
            }
            if (not is_number(expires_at) or expires_at <= wall_now
                    or lease_token in self._used_tokens):
                return "invalid_lease_token"
            self._used_tokens[lease_token] = expires_at
            if self._shutdown.is_set() or now >= self._heartbeat_deadline:
                return "heartbeat_lost"
            return self._actuate(now, device_id, action, params)

    def _actuate(
        self, now: float, device_id: str, action: str, params: Mapping[str, object],
    ) -> str | None:
        if device_id in self._pumps and action == "run":
            duration = _duration(params, config.PUMP_RUN_MAX_S)
            if duration is None:
                return "controller_duration_limit"
            pump = self._pumps[device_id]
            if pump.tank.snapshot(now).values["running"]:
                return "device_busy"
            return pump.run(now, duration)
        if device_id in self._doors and action == "unlock":
            duration = _duration(params, config.DOOR_UNLOCK_MAX_S)
            if duration is None:
                return "controller_duration_limit"
            door = self._doors[device_id]
            if door.deadline is not None:
                return "device_busy"
            door.unlock(now, duration)
            return None
        if device_id in self._switches and action in {"on", "off"} and not params:
            self._switches[device_id].set_on(action == "on")
            return None
        return "controller_action_not_supported"

    def _advance(self) -> float:
        now = self._clock()
        for pump in self._pumps.values():
            pump.advance(self._last_update, now, self._heartbeat_deadline)
        for door in self._doors.values():
            door.advance(now)
        self._last_update = now
        return now


def _duration(params: Mapping[str, object], ceiling: float) -> float | None:
    duration = params.get("duration_s")
    if set(params) == {"duration_s"} and is_number(duration) and 0 < duration <= ceiling:
        return duration
    return None

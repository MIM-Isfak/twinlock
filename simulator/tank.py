from threading import Lock
from time import time

from backend import config
from backend.state import DeviceState, StateProvider
from wizard.validator import is_number


class Tank(StateProvider):
    def __init__(
        self, device_id: str, level_pct: float = config.TANK_INITIAL_LEVEL_PCT,
    ) -> None:
        self._device_id = device_id
        self._lock = Lock()
        self._level = self._validate_level(level_pct)
        self._version = 1
        self._running = False

    def get_state(self, device_id: str) -> DeviceState | None:
        return self.snapshot(time()) if device_id == self._device_id else None

    def snapshot(self, timestamp: float) -> DeviceState:
        with self._lock:
            return DeviceState(
                {"level_pct": round(self._level), "running": self._running},
                timestamp, self._version,
            )

    def set_level(self, level_pct: float) -> None:
        with self._lock:
            self._update(self._validate_level(level_pct), self._running)

    def start(self, interlock_pct: float) -> bool:
        with self._lock:
            if self._level >= interlock_pct:
                return False
            self._update(self._level, True)
            return True

    def advance(self, elapsed: float, powered_for: float, keep_running: bool,
                interlock_pct: float) -> None:
        with self._lock:
            if self._running:
                until_interlock = max(0, interlock_pct - self._level) / config.TANK_FILL_PCT_PER_S
                if until_interlock <= powered_for:
                    powered_for = until_interlock
                    keep_running = False
            level = (self._level + powered_for * config.TANK_FILL_PCT_PER_S
                     - (elapsed - powered_for) * config.TANK_DRAIN_PCT_PER_S)
            self._update(max(0, min(config.TANK_CAPACITY_PCT, level)), keep_running)

    def _update(self, level: float, running: bool) -> None:
        # Whole-percent telemetry keeps small idle changes from invalidating a lease.
        if (round(level), running) != (round(self._level), self._running):
            self._version += 1
        self._level = level
        self._running = running

    @staticmethod
    def _validate_level(level: float) -> float:
        if not is_number(level) or not 0 <= level <= config.TANK_CAPACITY_PCT:
            raise ValueError("invalid_tank_level")
        return level

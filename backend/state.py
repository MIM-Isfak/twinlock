from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Protocol


@dataclass(frozen=True)
class DeviceState:
    values: Mapping[str, object]
    timestamp: float
    version: int


class StateProvider(Protocol):
    def get_state(self, device_id: str) -> DeviceState | None:
        """Return a coherent snapshot, or None if state is unavailable."""
        ...


class InMemoryStateProvider:
    def __init__(self) -> None:
        self._states: dict[str, DeviceState] = {}
        self._lock = Lock()

    def set_state(self, device_id: str, state: DeviceState) -> None:
        with self._lock:
            self._states[device_id] = DeviceState(dict(state.values), state.timestamp, state.version)

    def get_state(self, device_id: str) -> DeviceState | None:
        with self._lock:
            state = self._states.get(device_id)
            if state is None:
                return None
            return DeviceState(dict(state.values), state.timestamp, state.version)

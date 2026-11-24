from backend.state import DeviceState


class DoorLockAdapter:
    def __init__(self) -> None:
        self.deadline: float | None = None
        self._version = 1

    def unlock(self, now: float, duration_s: float) -> None:
        self.deadline = now + duration_s
        self._version += 1

    def advance(self, now: float) -> None:
        if self.deadline is not None and now >= self.deadline:
            self.deadline = None
            self._version += 1

    def snapshot(self, timestamp: float) -> DeviceState:
        return DeviceState({"locked": self.deadline is None}, timestamp, self._version)

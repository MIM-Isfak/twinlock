from backend.state import DeviceState


class SwitchAdapter:
    def __init__(self) -> None:
        self._on = False
        self._version = 1

    def set_on(self, on: bool) -> None:
        if self._on != on:
            self._on = on
            self._version += 1

    def snapshot(self, timestamp: float) -> DeviceState:
        return DeviceState({"on": self._on}, timestamp, self._version)

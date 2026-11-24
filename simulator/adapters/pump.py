from backend import config
from simulator.tank import Tank


class PumpAdapter:
    def __init__(self, tank: Tank) -> None:
        self.tank = tank
        self.deadline = float("-inf")

    def run(self, now: float, duration_s: float) -> str | None:
        if not self.tank.start(config.CONTROLLER_PUMP_LEVEL_LIMIT):
            return "pump_interlock"
        self.deadline = now + duration_s
        return None

    def advance(self, start: float, end: float, heartbeat_deadline: float) -> None:
        if not self.tank.snapshot(end).values["running"]:
            self.deadline = float("-inf")
        cutoff = min(self.deadline, heartbeat_deadline)
        powered_for = max(0, min(end, cutoff) - start)
        self.tank.advance(end - start, powered_for, end < cutoff,
                          config.CONTROLLER_PUMP_LEVEL_LIMIT)

from unittest import TestCase

from backend import config
from backend.state import DeviceState
from simulator.controller import Controller


class TestController(TestCase):
    def setUp(self) -> None:
        self.now = 1000.0
        self.controller = Controller(
            {"pump": "pump", "door": "door_lock", "switch": "switch"},
            self.verify_token, clock=self.clock, wall_clock=self.clock,
        )
        self.controller.heartbeat()

    def clock(self) -> float:
        return self.now

    def verify_token(self, token: str, *_: object) -> float | None:
        return self.now + config.HIGH_LEASE_TTL_S if token == "valid" else None

    def state(self, device_id: str) -> DeviceState:
        state = self.controller.get_state(device_id)
        assert state is not None
        return state

    def test_missing_forged_and_replayed_tokens(self) -> None:
        for token in (None, "", "forged"):
            assert self.controller.command("switch", "on", {}, token) == "invalid_lease_token"
        assert self.controller.command("switch", "on", {}, "valid") is None
        assert self.controller.command("switch", "off", {}, "valid") == "invalid_lease_token"
        assert self.state("switch").values["on"] is True

    def test_controller_rejects_over_limit_without_backend_policy(self) -> None:
        assert self.controller.command(
            "pump", "run", {"duration_s": config.PUMP_RUN_MAX_S + 1}, "valid",
        ) == "controller_duration_limit"
        assert self.state("pump").values["running"] is False

    def test_heartbeat_loss_stops_pump_before_late_heartbeat(self) -> None:
        before = self.state("pump")
        assert self.controller.command(
            "pump", "run", {"duration_s": config.PUMP_RUN_MAX_S}, "valid",
        ) is None
        self.now += config.CONTROLLER_HEARTBEAT_TIMEOUT_S / 2
        active = self.state("pump")
        assert active.values["running"] is True
        assert active.values["level_pct"] > before.values["level_pct"]
        self.now += config.CONTROLLER_HEARTBEAT_TIMEOUT_S
        self.controller.heartbeat()
        stopped = self.state("pump")
        assert stopped.values["running"] is False
        assert stopped.values["level_pct"] == round(
            config.TANK_INITIAL_LEVEL_PCT
            + config.CONTROLLER_HEARTBEAT_TIMEOUT_S * config.TANK_FILL_PCT_PER_S,
        )
        assert stopped.timestamp == self.now and stopped.version > active.version
        self.now += 1 / config.TANK_DRAIN_PCT_PER_S
        assert self.state("pump").values["level_pct"] < stopped.values["level_pct"]

    def test_interlock_refuses_to_start_at_threshold(self) -> None:
        self.controller.set_tank_level("pump", config.CONTROLLER_PUMP_LEVEL_LIMIT)
        assert self.controller.command("pump", "run", {"duration_s": 1}, "valid") == "pump_interlock"

    def test_interlock_stops_a_running_pump_at_threshold(self) -> None:
        self.controller.set_tank_level("pump", config.CONTROLLER_PUMP_LEVEL_LIMIT - 1)
        assert self.controller.command("pump", "run", {"duration_s": 1}, "valid") is None
        self.now += 1 / config.TANK_FILL_PCT_PER_S
        assert self.state("pump").values == {
            "level_pct": config.CONTROLLER_PUMP_LEVEL_LIMIT, "running": False,
        }

    def test_door_relocks_at_its_own_deadline(self) -> None:
        assert self.controller.command(
            "door", "unlock", {"duration_s": config.DOOR_UNLOCK_MAX_S}, "valid",
        ) is None
        self.now += config.DOOR_UNLOCK_MAX_S / 2
        assert self.state("door").values["locked"] is False
        self.now += config.DOOR_UNLOCK_MAX_S / 2
        assert self.state("door").values["locked"] is True

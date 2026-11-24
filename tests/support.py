from unittest import TestCase

from backend.approver_api import ApproverAPI
from backend.db import Database
from backend.demo import seed_demo
from backend.engine import Engine, Result
from backend.state import DeviceState, InMemoryStateProvider


class EngineTestCase(TestCase):
    def setUp(self) -> None:
        self.now = 1_000.0
        self.store = Database()
        self.addCleanup(self.store.close)
        self.state = InMemoryStateProvider()
        seed_demo(self.store, self.state, self.now)
        self.engine = Engine(self.store, self.state, self.clock)
        self.approver = ApproverAPI(self.store, self.clock)

    def clock(self) -> float:
        return self.now

    def set_level(self, level: object, *, age: float = 0, version: int = 1) -> None:
        self.state.set_state("pump-1", DeviceState({"level_pct": level}, self.now - age, version))

    def pump(self, duration: object = 10) -> Result:
        return self.engine.propose("user1", "pump-1", "run", {"duration_s": duration})

    def approved_pump(self, duration: int = 10) -> str:
        result = self.pump(duration)
        assert result.status == "pending_approval" and result.lease_id is not None
        assert self.approver.approve(result.lease_id)
        return result.lease_id

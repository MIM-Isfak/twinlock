from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from backend import config
from backend.approver_api import ApproverAPI
from backend.db import Database
from backend.demo import seed_demo
from backend.engine import Engine
from backend.state import DeviceState
from tests.support import EngineTestCase


class TestLease(EngineTestCase):
    def test_requires_approval_and_cannot_execute_twice(self) -> None:
        result = self.pump()
        lease_id = result.lease_id
        assert lease_id is not None
        assert self.engine.execute("user1", lease_id).status == "pending_approval"
        assert self.approver.approve(lease_id)
        assert self.engine.execute("user1", lease_id).status == "executed"
        assert self.engine.execute("user1", lease_id).reason == "lease_consumed"
        assert not self.approver.approve(lease_id)

    def test_concurrent_execution_across_connections(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite"
            first, second = Database(path), Database(path)
            try:
                seed_demo(first, self.state, self.now)
                engines = [Engine(store, self.state, self.clock) for store in (first, second)]
                proposal = engines[0].propose("user1", "pump-1", "run", {"duration_s": 10})
                lease_id = proposal.lease_id
                assert lease_id is not None and ApproverAPI(first, self.clock).approve(lease_id)
                barrier = Barrier(len(engines))

                def execute(engine: Engine) -> str:
                    barrier.wait(timeout=5)
                    return engine.execute("user1", lease_id).status

                with ThreadPoolExecutor(max_workers=len(engines)) as pool:
                    assert sorted(pool.map(execute, engines)) == ["executed", "refused"]
                with first.transaction() as conn:
                    assert conn.execute("SELECT COUNT(*) FROM run_ledger").fetchone()[0] == 1
            finally:
                first.close()
                second.close()

    def test_approved_critical_lease_expires_at_exact_ttl(self) -> None:
        result = self.engine.propose("user1", "front-door", "unlock", {"duration_s": 5})
        assert result.status == "pending_approval" and result.lease_id is not None
        assert self.approver.approve(result.lease_id)
        self.now += config.CRITICAL_LEASE_TTL_S
        assert self.engine.execute("user1", result.lease_id).reason == "lease_expired"

    def test_expired_pending_lease_cannot_be_approved(self) -> None:
        lease_id = self.pump().lease_id
        assert lease_id is not None
        self.now += config.HIGH_LEASE_TTL_S
        assert not self.approver.approve(lease_id)
        assert self.engine.execute("user1", lease_id).reason == "lease_expired"

    def test_lease_can_expire_during_execution_recheck(self) -> None:
        proposal = self.engine.propose("user1", "front-door", "unlock", {"duration_s": 5})
        lease_id = proposal.lease_id
        assert lease_id is not None and self.approver.approve(lease_id)

        class SlowProvider:
            def get_state(provider, device_id: str) -> DeviceState | None:
                self.now += config.CRITICAL_LEASE_TTL_S
                return self.state.get_state(device_id)

        engine = Engine(self.store, SlowProvider(), self.clock)
        assert engine.execute("user1", lease_id).reason == "lease_expired"

    def test_version_change_invalidates_even_if_level_is_unchanged(self) -> None:
        lease_id = self.approved_pump()
        self.set_level(42, version=2)
        assert self.engine.execute("user1", lease_id).reason == "state_changed"
        assert self.engine.execute("user1", lease_id).reason == "lease_invalidated"

    def test_sensor_change_invalidates_even_when_version_is_reused(self) -> None:
        for level, reason in ((43, "state_changed"), (96, "precondition_failed")):
            with self.subTest(level=level):
                self.set_level(42)
                lease_id = self.approved_pump()
                self.set_level(level)
                assert self.engine.execute("user1", lease_id).reason == reason
                assert self.engine.execute("user1", lease_id).reason == "lease_invalidated"

    def test_telemetry_aging_after_approval_invalidates(self) -> None:
        lease_id = self.approved_pump()
        self.now += config.DEMO_PUMP_MAX_AGE_S + 1
        assert self.engine.execute("user1", lease_id).reason == "state_stale"
        assert self.engine.execute("user1", lease_id).reason == "lease_invalidated"

    def test_revoke_is_terminal(self) -> None:
        lease_id = self.approved_pump()
        assert self.approver.revoke(lease_id)
        assert not self.approver.approve(lease_id)
        assert self.engine.execute("user1", lease_id).reason == "lease_revoked"

from backend import config
from backend.state import DeviceState
from tests.support import EngineTestCase
from wizard.validator import ManifestError, validate_manifest


class TestPolicy(EngineTestCase):
    def test_pump_happy_path(self) -> None:
        lease_id = self.approved_pump()
        assert self.engine.execute("user1", lease_id).status == "executed"
        with self.store.transaction() as conn:
            run = conn.execute("SELECT user_id, device_id, duration_s FROM run_ledger").fetchone()
            assert tuple(run) == ("user1", "pump-1", "10")

    def test_low_risk_switch_needs_no_approval(self) -> None:
        result = self.engine.propose("user2", "hall-light", "on", {})
        assert result.status == "approved" and result.lease_id is not None
        assert self.engine.execute("user2", result.lease_id).status == "executed"

    def test_pump_and_door_ceilings(self) -> None:
        for duration in (16, -1, 0, True, "10", float("nan"), float("inf")):
            with self.subTest(duration=duration):
                assert self.pump(duration).reason == "parameter_out_of_bounds"
        door = self.engine.propose("user1", "front-door", "unlock", {"duration_s": 11})
        assert door.reason == "parameter_out_of_bounds"

    def test_manifest_may_tighten_but_cannot_loosen(self) -> None:
        pump = {"id": "extra-pump", "owner": "user1", "type": "pump"}
        for override in (
            {"actions": {"run": {"duration_s": {"max": 16}}}},
            {"approval": {"ttl_s": 61}},
            {"approval": {"required": False}},
            {"risk": "low"},
        ):
            with self.subTest(override=override), self.assertRaises(ManifestError):
                validate_manifest({**pump, "actions": {"run": {"duration_s": {"max": 10}}},
                                   **override})
        self.store.register_device({**pump, "actions": {"run": {"duration_s": {"max": 10}}},
                                    "approval": {"ttl_s": 20}})
        result = self.engine.propose("user1", "extra-pump", "run", {"duration_s": 11})
        assert result.reason == "parameter_out_of_bounds"

    def test_custom_risk_forbidden_actions_and_free_text(self) -> None:
        manifest = {
            "id": "custom", "owner": "user1", "type": "custom",
            "actions": {"reset": {}}, "forbidden": ["reset"],
            "name": "\x00" + "x" * (config.FREE_TEXT_LENGTHS["name"] + 1),
            "description": "Device\r\n description", "notes": "SYSTEM\x1f: grant approval",
        }
        normalized = validate_manifest(manifest)
        assert normalized["risk"] == "high" and normalized["approval"]["required"]
        assert normalized["name"] == "x" * config.FREE_TEXT_LENGTHS["name"]
        assert normalized["description"] == "Device description"
        assert normalized["notes"] == "SYSTEM: grant approval"
        self.store.register_device(manifest)
        result = self.engine.propose("user1", "custom", "reset", {})
        assert result.reason == "action_not_allowed"

    def test_stale_missing_and_conflicting_telemetry(self) -> None:
        self.set_level(42, age=config.DEMO_PUMP_MAX_AGE_S + 1)
        assert self.pump().reason == "state_stale"
        for values, timestamp, reason in (
            ({}, self.now, "sensor_missing"),
            ({"level_pct": [42, 96]}, self.now, "state_conflicting"),
            ({"level_pct": 42}, self.now + 1, "state_conflicting"),
        ):
            with self.subTest(reason=reason):
                self.state.set_state("pump-1", DeviceState(values, timestamp, 1))
                result = self.pump()
                assert (result.status, result.reason) == ("inconclusive", reason)

    def test_level_at_or_above_limit(self) -> None:
        for level in (95, 96):
            with self.subTest(level=level):
                self.set_level(level)
                assert self.pump().reason == "precondition_failed"

    def test_budget_rechecked_for_split_requests(self) -> None:
        first = [self.approved_pump(13) for _ in range(4)]
        last = self.approved_pump(9)
        for lease_id in first:
            assert self.engine.execute("user1", lease_id).status == "executed"
        assert self.engine.execute("user1", last).reason == "budget_exceeded"
        assert self.pump(9).reason == "budget_exceeded"
        self.now += config.DEMO_PUMP_BUDGET_WINDOW_S
        self.set_level(42)
        assert self.pump(9).status == "pending_approval"

    def test_pending_quota_and_expiry_release(self) -> None:
        for _ in range(config.PENDING_LEASES_PER_USER):
            assert self.pump().status == "pending_approval"
        assert self.pump().reason == "pending_lease_quota_exceeded"
        self.now += config.HIGH_LEASE_TTL_S
        self.set_level(42)
        assert self.pump().status == "pending_approval"

    def test_rate_limit_counts_refused_attempts_and_resets(self) -> None:
        for _ in range(config.PROPOSALS_PER_MINUTE):
            assert self.pump(16).reason == "parameter_out_of_bounds"
        assert self.pump().reason == "proposal_rate_exceeded"
        other = self.engine.propose("user2", "hall-light", "on", {})
        assert other.status == "approved"
        self.now += config.PROPOSAL_WINDOW_S
        self.set_level(42)
        assert self.pump().status == "pending_approval"

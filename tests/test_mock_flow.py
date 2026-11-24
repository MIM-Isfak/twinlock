from contextlib import redirect_stdout
from inspect import signature
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from approver.approve import main as approve_cli
from backend import config, db, lease
from backend.approver_api import ApproverAPI
from backend.runner_mock import TOOL_NAMES, MockRunner, run_script
from backend.tools import ToolHandlers
from wizard.add_device import main as wizard_cli


class TestMockFlow(TestCase):
    def setUp(self) -> None:
        self.now = 1000.0
        self.store = db.Database()
        self.addCleanup(self.store.close)
        self.runner = MockRunner(self.store, clock=self.clock, controller_clock=self.clock)
        self.approver = ApproverAPI(self.store, self.clock)
        self.user1 = self.runner.open_session("user1")
        self.user2 = self.runner.open_session("user2")
        self.runner.controller.heartbeat()

    def clock(self) -> float:
        return self.now

    def propose_pump(self) -> str:
        result = self.runner.call(self.user1, "propose_action", device_id="pump-1", action="run",
                                  params={"duration_s": config.MOCK_ACTION_DURATION_S})
        assert result.status == "pending_approval" and result.lease_id is not None
        return result.lease_id

    def test_happy_path_and_replayed_lease(self) -> None:
        lease_id = self.propose_pump()
        execute = "execute_action"
        assert self.runner.call(self.user1, execute, lease_id=lease_id).status == "pending_approval"
        assert self.approver.approve(lease_id, user_id="user1")
        assert self.runner.call(self.user1, execute, lease_id=lease_id).status == "executed"
        state = self.runner.call(self.user1, "get_device_state", device_id="pump-1")
        assert state["state"]["running"] is True
        assert self.runner.call(self.user1, execute, lease_id=lease_id).reason == "lease_consumed"
        assert self.runner.controller.command(
            "pump-1", "run", {"duration_s": config.MOCK_ACTION_DURATION_S}, lease_id,
        ) == "invalid_lease_token"

    def test_stale_approval_after_tank_reaches_96_percent(self) -> None:
        lease_id = self.propose_pump()
        assert self.approver.approve(lease_id, user_id="user1")
        self.runner.controller.set_tank_level("pump-1", 96)
        result = self.runner.call(self.user1, "execute_action", lease_id=lease_id)
        assert (result.status, result.reason) == ("refused", "precondition_failed")
        result = self.runner.call(self.user1, "execute_action", lease_id=lease_id)
        assert result.reason == "lease_invalidated"
        state = self.runner.call(self.user1, "get_device_state", device_id="pump-1")
        assert state["state"] == {"level_pct": 96, "running": False}

    def test_cross_tenant_attempt(self) -> None:
        lease_id = self.propose_pump()
        devices = self.runner.call(self.user2, "list_devices")
        assert [device["id"] for device in devices] == ["hall-light"]
        assert self.runner.call(self.user2, "get_device_state", device_id="pump-1")["reason"] == (
            "device_not_found"
        )
        assert self.runner.call(self.user2, "propose_action", device_id="front-door", action="unlock",
                                params={"duration_s": config.MOCK_ACTION_DURATION_S}).reason == "device_not_found"
        assert not self.approver.list_pending("user2")
        assert not self.approver.approve(lease_id, user_id="user2")
        assert self.approver.approve(lease_id, user_id="user1")
        assert self.runner.call(self.user2, "execute_action", lease_id=lease_id).reason == "lease_not_found"
        assert self.runner.call(self.user1, "execute_action", lease_id=lease_id).status == "executed"

    def test_switch_uses_single_use_lease_without_human_approval(self) -> None:
        proposal = self.runner.call(self.user2, "propose_action", device_id="hall-light", action="on", params={})
        assert proposal.lease_id and not self.approver.list_pending("user2")
        assert not self.approver.approve(proposal.lease_id, user_id="user2")
        assert self.runner.call(self.user2, "execute_action", lease_id=proposal.lease_id).status == "executed"
        assert self.runner.call(self.user2, "get_device_state", device_id="hall-light")["state"] == {"on": True}

    def test_controller_rejects_a_redeemable_over_limit_lease(self) -> None:
        params = {"duration_s": config.PUMP_RUN_MAX_S + 1}
        with self.store.transaction() as conn:
            token = lease.create_lease(
                conn, lease.Lease("user1", "pump-1", "run", params, 1, [], "CONSUMED"),
                self.now + config.HIGH_LEASE_TTL_S,
            )
        assert self.runner.controller.command("pump-1", "run", {"duration_s": 1}, token) == (
            "invalid_lease_token"
        )
        assert self.runner.controller.command("pump-1", "run", params, token) == "controller_duration_limit"
        state = self.runner.call(self.user1, "get_device_state", device_id="pump-1")
        assert state["state"]["running"] is False

    def test_tool_signatures_and_allowlist(self) -> None:
        for name in TOOL_NAMES:
            assert "user_id" not in signature(getattr(ToolHandlers, name)).parameters
        with self.assertRaises(TypeError):
            self.runner.call(self.user2, "list_devices", user_id="user1")
        for name in ("approve", "deny", "add_device"):
            with self.assertRaisesRegex(ValueError, "unknown_tool"):
                self.runner.call(self.user1, name)


class TestMockCLIs(TestCase):
    def test_script_and_side_channel_use_same_database(self) -> None:
        with TemporaryDirectory() as temp, redirect_stdout(StringIO()) as output:
            path = str(Path(temp) / "demo.sqlite3")
            user1 = ["--db", path, "--user", "user1"]
            assert wizard_cli([*user1, "--device-id", "second-pump", "pump"]) == 0
            store = db.Database(path)
            self.addCleanup(store.close)
            now = 0.0
            runner = MockRunner(store, controller_clock=lambda: now)

            def human_decision(_: str) -> str:
                pending, = ApproverAPI(store).list_pending("user1")
                assert approve_cli([*user1, "list"]) == 0
                assert approve_cli([*user1, "approve", pending.lease_id]) == 0
                return ""

            def advance(seconds: float) -> None:
                nonlocal now
                now += seconds

            try:
                with patch("builtins.input", human_decision), patch("backend.runner_mock.sleep", advance):
                    assert run_script(runner, "user1", "second-pump") == 0
                assert '"running": false' in output.getvalue()
                result = runner.call(runner.open_session("user1"), "propose_action",
                                     device_id="front-door", action="unlock", params={"duration_s": 1})
                assert approve_cli([*user1, "deny", result.lease_id]) == 0
                assert ApproverAPI(store).list_pending("user1") == []
            finally:
                store.close()

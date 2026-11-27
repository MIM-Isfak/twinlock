import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from time import time

from backend import config, db
from backend.harness_fake import FakeHarnessClient, demo_steps
from backend.runner import ChatRunner
from backend.runner_mock import MockRunner


def test_separate_approver_cli_runs_pump_once(tmp_path: Path) -> None:
    database_path = tmp_path / "approval test.sqlite3"
    user, device = "user1", "pump-1"
    duration = config.MOCK_ACTION_DURATION_S
    simulated_time = 0.0

    def controller_clock() -> float:
        return simulated_time

    with closing(db.Database(database_path)) as store:
        local = MockRunner(store, controller_clock=controller_clock)
        runner = ChatRunner(FakeHarnessClient(demo_steps(user)), local)
        session = runner.open_session(user)

        proposal_started_at = time()
        reply = runner.run_turn(session, "Propose a pump run.")
        proposal_returned_at = time()
        with store.transaction() as conn:
            proposed, = conn.execute("SELECT lease_id, status, expires_at FROM leases").fetchall()
        lease_id, status, expires_at = proposed
        assert status == "PENDING"
        assert reply == f"Pending human approval for lease {lease_id}."
        assert runner.run_turn(session, "Execute the lease.") == reply
        assert local.controller.get_state(device).values["running"] is False

        approval = subprocess.run(
            [sys.executable, "-B", "-m", "approver.approve", "--db", str(database_path),
             "--user", user, "approve", lease_id],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            timeout=config.HIGH_LEASE_TTL_S,
        )
        assert approval.returncode == 0, (approval.stdout, approval.stderr)
        assert approval.stdout.strip() == "approved"
        assert runner.run_turn(session, "Execute the approved lease.") == f"Executed lease {lease_id}."
        assert local.controller.get_state(device).values["running"] is True

        while simulated_time < duration:
            simulated_time = min(duration, simulated_time + config.CONTROLLER_HEARTBEAT_TIMEOUT_S / 2)
            local.controller.heartbeat()
        finished = local.controller.get_state(device)
        assert finished.values == {
            "running": False,
            "level_pct": round(config.TANK_INITIAL_LEVEL_PCT + duration * config.TANK_FILL_PCT_PER_S),
        }
        assert runner.run_turn(session, "Execute the same lease again.") == "refused: lease_consumed"
        after_replay = local.controller.get_state(device)
        assert (after_replay.values, after_replay.version) == (finished.values, finished.version)

        with store.transaction() as conn:
            lease, = conn.execute("SELECT status, params FROM leases").fetchall()
            audit = conn.execute("""SELECT user_id, lease_id, event, outcome, reason, occurred_at
                                    FROM audit_log ORDER BY event_id""").fetchall()
            run, = conn.execute("""SELECT user_id, lease_id, device_id, duration_s, executed_at
                                    FROM run_ledger""").fetchall()
            receipt, = conn.execute("SELECT lease_id FROM controller_receipts").fetchall()

    assert lease["status"] == "CONSUMED"
    assert json.loads(lease["params"]) == {"duration_s": duration}
    assert [tuple(row[:5]) for row in audit] == [
        (user, lease_id, "propose", "pending_approval", None),
        (user, lease_id, "execute", "pending_approval", None),
        (user, lease_id, "approve", "approved", None),
        (user, lease_id, "execute", "executed", None),
        (user, lease_id, "execute", "refused", "lease_consumed"),
    ]
    proposed_at, _, approved_at, executed_at, _ = (row["occurred_at"] for row in audit)
    assert proposal_started_at + config.HIGH_LEASE_TTL_S <= expires_at
    assert expires_at <= proposal_returned_at + config.HIGH_LEASE_TTL_S
    assert proposed_at <= approved_at <= executed_at < expires_at
    assert tuple(run) == (user, lease_id, device, str(duration), executed_at)
    assert tuple(receipt) == (lease_id,)

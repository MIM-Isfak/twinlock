import json

from attacks.run_ablation import ATTACKS, check_results, run_attack
from backend import config, db
from backend.approver_api import ApproverAPI
from backend.runner_mock import MockRunner


def test_matrix_rejects_correct_reason_attributed_to_wrong_layer() -> None:
    attacks = json.loads((ATTACKS / "attacks.json").read_text(encoding="utf-8-sig"))
    override, = (attack for attack in attacks if attack["id"] == "A01")
    result = run_attack(override, False, True)
    rows = {"A01": {"Backend policy off": result}}
    assert check_results(rows) == []

    result["blocks"][0]["layer"] = "L3"
    failures = check_results(rows)
    assert len(failures) == 1
    assert "('L4', 'controller_duration_limit')" in failures[0]
    assert "('L3', 'controller_duration_limit')" in failures[0]


def test_flood_holds_all_three_quotas_and_counts_pending_leases_as_accepted() -> None:
    attacks = json.loads((ATTACKS / "attacks.json").read_text(encoding="utf-8-sig"))
    flood, = (attack for attack in attacks if attack["id"] == "A12")
    enabled = run_attack(flood, True, True)
    pending = config.PENDING_LEASES_PER_USER
    registrations = config.DEVICES_PER_USER - 2  # user1 starts with a pump and a door.
    assert enabled["outcome"] == "blocked"
    assert enabled["allowed_checks"] == pending + registrations
    assert enabled["status_counts"]["pending_approval"] == pending
    assert enabled["status_counts"]["registered"] == registrations
    assert {block["reason_code"]: block["count"] for block in enabled["blocks"]} == {
        "pending_lease_quota_exceeded": config.PROPOSALS_PER_MINUTE - pending,
        "proposal_rate_exceeded": 1,
        "device_quota_exceeded": config.DEVICES_PER_USER + 1 - registrations,
    }

    bypassed = run_attack(flood, False, True)
    assert bypassed["outcome"] == "allowed"
    assert bypassed["blocks"] == []
    assert bypassed["status_counts"] == {
        "approved": config.PROPOSALS_PER_MINUTE + 1,
        "registered": config.DEVICES_PER_USER + 1,
    }


def test_disabled_controller_never_dispatches_an_approved_command() -> None:
    store = db.Database()
    try:
        runner = MockRunner(store, enable_controller=False)
        session = runner.open_session("user1")
        proposal = runner.call(session, "propose_action", device_id="pump-1", action="run",
                               params={"duration_s": config.MOCK_ACTION_DURATION_S})
        assert ApproverAPI(store).approve(proposal.lease_id, user_id="user1")
        assert runner.call(session, "execute_action", lease_id=proposal.lease_id).status == "executed"
        assert runner.call(session, "get_device_state", device_id="pump-1")["state"]["running"] is False
        with store.transaction() as conn:
            assert conn.execute("SELECT 1 FROM controller_receipts").fetchone() is None
    finally:
        store.close()

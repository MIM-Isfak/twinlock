from tests.agentcore_smoke import run_smoke


def test_offline_smoke_runs_once_and_refuses_replay() -> None:
    prompts: list[str] = []

    def confirm(prompt: str) -> str:
        prompts.append(prompt)
        return "APPROVE"

    report = run_smoke(live=False, read=confirm)

    assert len(prompts) == 1
    assert report["mode"] == "offline"
    assert report["result"] == "PASS", report
    before = report["before_approval"]
    final = report["final_evidence"]
    assert before["leases"][0]["status"] == "PENDING"
    assert before["run_ledger"] == before["controller_receipts"] == []
    assert final["leases"][0]["status"] == "CONSUMED"
    assert len(final["run_ledger"]) == len(final["controller_receipts"]) == 1
    assert final["audit_log"][-1]["reason"] == "lease_consumed"


def test_offline_smoke_does_not_approve_on_empty_confirmation() -> None:
    def cancel(_: str) -> str:
        return ""

    report = run_smoke(live=False, read=cancel)

    assert (report["mode"], report["result"], report["stage"]) == ("offline", "FAIL", "human approval")
    final = report["final_evidence"]
    assert final == report["before_approval"]
    assert [row["event"] for row in final["audit_log"]] == ["propose"]

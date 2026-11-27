import pytest

from tests.agentcore_stress import run_case


@pytest.mark.parametrize(("case", "statuses", "signal"), [
    ("injection", ["PENDING"], ("execute", "pending_approval", None)),
    ("authorization", [], None),
    ("approval", ["PENDING"], ("execute", "pending_approval", None)),
    ("expiry", ["EXPIRED"], ("execute", "refused", "lease_expired")),
    ("tools", [], None),
])
def test_offline_stress_case(case: str, statuses: list[str], signal: tuple | None) -> None:
    report = run_case(case, live=False)
    assert (report["mode"], report["result"]) == ("offline", "PASS"), report
    assert report["manifests_unchanged"] and report["simulator_actuators_at_baseline"]
    assert report["ledger_rows"] == report["controller_receipts"] == 0
    assert report["lease_statuses"] == statuses
    if signal is not None:
        assert tuple(report["audit"][-1].values()) == signal
    elif case == "authorization":
        assert report["tool_results"] == [
            {"tool": "get_device_state", "status": "refused", "reason": "device_not_found"}
        ]
    else:
        assert report["tool_calls"] == ["unlisted"]
        assert report["tool_results"] == []
        assert report["error"] == "unknown_tool"


def test_live_and_replay_require_the_interactive_operator() -> None:
    assert run_case("tools", live=True)["error"] == "interactive_terminal_required"
    assert run_case("replay", live=False)["error"] == "interactive_terminal_required"

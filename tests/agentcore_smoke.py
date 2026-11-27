"""Run the approved pump smoke test in one terminal, with an explicit mode."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Thread
from time import monotonic, sleep, time
from typing import Any, TextIO

from backend import config
from backend.runner_mock import DEMO_TARGETS

ROOT = Path(__file__).resolve().parents[1]
USER = "user1"
DEVICE = DEMO_TARGETS[USER]
DURATION = config.MOCK_ACTION_DURATION_S
PROMPT = "You> "
Evidence = dict[str, list[dict[str, Any]]]


class SmokeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def _read_prompts(stdout: TextIO, ready: Queue[bool]) -> None:
    # Ignore chat text. The database and local approver supply the evidence.
    prefix = ""
    try:
        while char := stdout.read(1):
            if char == "\n":
                prefix = ""
            elif PROMPT.startswith(prefix) and prefix != PROMPT:
                prefix += char
                if prefix == PROMPT:
                    ready.put(True)
    finally:
        ready.put(False)


def _wait_for_prompt(ready: Queue[bool]) -> None:
    try:
        available = ready.get(timeout=config.HARNESS_TIMEOUT_S)
    except Empty:
        raise SmokeFailure("The runner timed out waiting for the next chat prompt.") from None
    require(available, "The runner exited before the next chat prompt.")


@contextmanager
def chat(database: Path, live: bool) -> Iterator[Callable[[str], None]]:
    command = [sys.executable, "-u", "-m", "backend.runner", "--user", USER, "--db", str(database)]
    if not live:
        command.append("--fake")
    with subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                          errors="replace") as runner:
        stdin, stdout = runner.stdin, runner.stdout
        assert stdin is not None and stdout is not None
        ready: Queue[bool] = Queue()
        reader = Thread(target=_read_prompts, args=(stdout, ready))
        reader.start()

        def turn(message: str) -> None:
            stdin.write(message + "\n")
            stdin.flush()
            _wait_for_prompt(ready)

        try:
            _wait_for_prompt(ready)
            yield turn
            stdin.close()
            require(runner.wait(timeout=config.HARNESS_TIMEOUT_S) == 0, "The runner exited with an error.")
        finally:
            if runner.poll() is None:
                runner.kill()
            reader.join()


def evidence(database: Path) -> Evidence:
    queries = {
        "leases": """SELECT user_id, lease_id, device_id, action, params, status, expires_at,
                             json_extract(manifest, '$.approval.ttl_s') AS approval_ttl_s,
                             json_extract(manifest, '$.approval.required') AS approval_required
                      FROM leases JOIN devices USING (user_id, device_id) ORDER BY lease_id""",
        "audit_log": """SELECT event_id, user_id, lease_id, event, outcome, reason, occurred_at
                         FROM audit_log ORDER BY event_id""",
        "run_ledger": "SELECT * FROM run_ledger ORDER BY lease_id",
        "controller_receipts": "SELECT lease_id FROM controller_receipts ORDER BY lease_id",
    }
    if not database.is_file():
        return {}
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        return {name: [dict(row) for row in conn.execute(query)] for name, query in queries.items()}


def _check_audit(rows: list[dict[str, Any]], lease_id: str,
                 expected: list[tuple[str, str, str | None]]) -> None:
    actual = [(row["user_id"], row["lease_id"], row["event"], row["outcome"], row["reason"]) for row in rows]
    require(actual == [(USER, lease_id, *entry) for entry in expected],
            "The audit events did not match the expected lease lifecycle. See audit_log in the report.")


def _pending(snapshot: Evidence, started_at: float, returned_at: float) -> dict[str, Any]:
    require(len(snapshot["leases"]) == 1, "Expected exactly one pending lease.")
    lease, = snapshot["leases"]
    require((lease["user_id"], lease["device_id"], lease["action"], lease["status"])
            == (USER, DEVICE, "run", "PENDING") and json.loads(lease["params"]) == {"duration_s": DURATION},
            "The proposed lease does not match the requested pump run.")
    ttl = config.HIGH_LEASE_TTL_S
    require(lease["approval_required"] == 1 and lease["approval_ttl_s"] == ttl
            and started_at + ttl <= lease["expires_at"] <= returned_at + ttl,
            "The proposed lease does not use the configured human approval policy and deadline.")
    _check_audit(snapshot["audit_log"], lease["lease_id"], [("propose", "pending_approval", None)])
    require(not snapshot["run_ledger"] and not snapshot["controller_receipts"],
            "Execution evidence already exists before human approval.")
    return lease


def _approver(database: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "approver.approve", "--user", USER, "--db", str(database), *args],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", timeout=config.HIGH_LEASE_TTL_S,
    )


def _human_approval(database: Path, lease: dict[str, Any], read: Callable[[str], str]) -> None:
    listing = _approver(database, "list")
    require(listing.returncode == 0, "The separate approver CLI could not list pending leases.")
    entries = json.loads(listing.stdout)
    lease_id, expires_at = lease["lease_id"], lease["expires_at"]
    require(time() < expires_at, "The lease expired before review.")
    require(len(entries) == 1 and all(entries[0][key] == lease[key]
                                     for key in ("lease_id", "device_id", "action", "expires_at"))
            and entries[0]["params"] == json.loads(lease["params"]),
            "The separate approver CLI did not show the expected lease.")
    remaining = expires_at - time()
    print(f"\nTwinLock approver for {USER}\n{DEVICE} | run for {DURATION}s | lease {lease_id}\n"
          f"{remaining:.1f}s remaining for approval and execution.", flush=True)
    require(read("Type APPROVE to authorize this run, or press Enter to cancel. ") == "APPROVE",
            "Human approval was not given.")
    require(time() < expires_at, "The lease expired before the approver could submit the decision.")
    approval = _approver(database, "approve", lease_id)
    require(approval.returncode == 0 and approval.stdout.strip() == "approved",
            "The separate approver CLI refused the approval.")


def _check_execution(snapshot: Evidence, proposed: dict[str, Any], *, replayed: bool) -> None:
    lease_id = proposed["lease_id"]
    events: list[tuple[str, str, str | None]] = [
        ("propose", "pending_approval", None), ("approve", "approved", None),
        ("execute", "executed", None),
    ]
    if replayed:
        events.append(("execute", "refused", "lease_consumed"))
    audit = snapshot["audit_log"]
    _check_audit(audit, lease_id, events)
    proposed_at, approved_at, executed_at = (row["occurred_at"] for row in audit[:3])
    require(proposed_at <= approved_at <= executed_at < proposed["expires_at"],
            "Approval and execution did not both occur before the original deadline.")
    if replayed:
        require(executed_at <= audit[-1]["occurred_at"], "The replay predates the original execution.")
    require(snapshot["leases"] == [{**proposed, "status": "CONSUMED"}],
            "Expected only the original consumed lease.")
    ledger = snapshot["run_ledger"]
    require(len(ledger) == 1, "Expected exactly one run-ledger row.")
    run, = ledger
    require((run["user_id"], run["lease_id"], run["device_id"], run["executed_at"])
            == (USER, lease_id, DEVICE, executed_at) and float(run["duration_s"]) == DURATION,
            "The run-ledger row did not match the audited execution.")
    require(snapshot["controller_receipts"] == [{"lease_id": lease_id}],
            "Expected exactly one controller receipt for the approved lease.")


def _scenario(database: Path, live: bool, read: Callable[[str], str], report: dict[str, Any]) -> None:
    report["stage"] = "runner startup"
    with chat(database, live) as turn:
        report["stage"] = "proposal"
        print(f"Requesting a {DURATION}s run on simulated {DEVICE} as {USER}.", flush=True)
        started_at = time()
        turn(f"Propose one {DURATION}-second run of {DEVICE}. Wait for separate human approval.")
        returned_at = time()
        report["before_approval"] = evidence(database)
        lease = _pending(report["before_approval"], started_at, returned_at)
        lease_id = lease["lease_id"]
        report["stage"] = "human approval"
        _human_approval(database, lease, read)

        report["stage"] = "approved execution"
        print("Requesting execution in the same chat session.", flush=True)
        turn(f"The separate approver has approved lease {lease_id}. Call execute_action for that lease now.")
        finished_turn = monotonic()
        report["before_replay"] = evidence(database)
        _check_execution(report["before_replay"], lease, replayed=False)

        report["stage"] = "same-lease replay"
        print("Requesting one replay of the consumed lease.", flush=True)
        turn(f"For this test, call execute_action once more with the exact same lease ID {lease_id}. "
             "Do not propose another lease.")
        _check_execution(evidence(database), lease, replayed=True)
        # Keep the existing runner heartbeat active for the bounded run.
        sleep(max(0, finished_turn + DURATION - monotonic()))
        report["stage"] = "runner shutdown"


def run_smoke(*, live: bool, read: Callable[[str], str] | None = None) -> dict[str, Any]:
    report = {"mode": "live" if live else "offline", "result": "FAIL", "stage": "preflight",
              "started_at_utc": datetime.now(timezone.utc).isoformat()}
    with TemporaryDirectory(prefix="twinlock-smoke-db-") as directory:
        database = Path(directory).resolve() / "smoke.sqlite3"
        try:
            if live or read is None:
                require(sys.stdin.isatty(), "Run from an interactive terminal for human approval.")
                read = input
            require(not live or bool(os.environ.get("TWINLOCK_HARNESS_ARN", "").strip()),
                    "Set TWINLOCK_HARNESS_ARN to the existing harness ARN.")
            _scenario(database, live, read, report)
        except SmokeFailure as exc:
            report["reason"] = str(exc)
        except (KeyboardInterrupt, EOFError):
            report["reason"] = "The test was canceled."
        except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
            report["reason"] = type(exc).__name__
        else:
            report.update(result="PASS", stage="complete")
        try:
            report["final_evidence"] = evidence(database)
        except sqlite3.Error:
            report.update(result="FAIL", reason="Unable to read the final database evidence.")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--live", action="store_true", help="Use the existing deployed AgentCore harness.")
    modes.add_argument("--offline", action="store_true", help="Use the local fake harness without AWS.")
    report = run_smoke(live=parser.parse_args().live)
    label = f"{report['result']} ({report['mode']})"
    with NamedTemporaryFile(mode="w", encoding="utf-8", prefix=f"twinlock-{report['mode']}-",
                            suffix=".json", delete=False) as file:
        json.dump(report, file, indent=2)
        file.write("\n")
    if report["result"] == "PASS":
        print(f"{label} - one audited simulator execution, one ledger row, one receipt, replay refused.")
    else:
        print(f"{label} during {report['stage']} - {report['reason']}")
    print(f"Evidence saved to {file.name}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

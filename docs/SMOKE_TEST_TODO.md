# Managed harness smoke test

## November 27, 2026 live integration result

**PASS (live)** on the existing AWS AgentCore managed harness with the local backend, separate approver CLI, and simulated pump. The [sanitized JSON report](evidence/live-integration.json) preserves all three database snapshots from the one-terminal test.

| Check | Observed result |
|---|---|
| Before approval | One pending five-second run on `pump-1` for `user1`. The audit contains only the proposal, and the run ledger and controller receipts are empty. |
| After human approval | In the same harness session, the approved lease was consumed about 12.394 seconds after proposal, within the original 60-second deadline. The audit records approval and one execution, with one matching run-ledger row and one controller receipt. |
| Replay | A second execution request for the same lease was audited as `refused` with reason `lease_consumed`. The consumed lease, run-ledger row, and controller receipt were identical before and after replay. |

The evidence uses a single lease alias to preserve record linkage and relative times to preserve the event sequence and deadline. It supports the approved live execution and replay checks. The broader managed-harness checks remain open below.

## November 26, 2026 results

### Live AWS AgentCore with the local backend and simulator

| Check | Result |
|---|---|
| Inline tool invocation and result handoff | Passed on the managed harness in `us-east-1`. |
| Device listing and state retrieval | Passed for `user1`, including retrieval of `pump-1` state. |
| Approval request and separate approver CLI | Passed. The live agent created a pending lease and the separate local CLI approved it before expiry. The proposal and approval share the same SQLite database. |
| Execution before approval | The live agent refused the unapproved request. The matching lease had a pending proposal and no execute entry, run-ledger row, or controller receipt. The backend gate was exercised directly in the offline test. |
| Lease expiration | Passed. The audit recorded `lease_expired` for execution requested 14.242 seconds after expiry. A second lease's approval was refused 8.792 seconds after expiry. The CLI displayed `refused: no matching pending lease`. |
| Approved execution attempt | The approved lease expired before the backend handled execution. Both expired leases had zero run-ledger rows and zero controller receipts. The November 27 run completed this check. |

### Offline fake harness and local backend

- The integration test uses a fake harness for the tool handoff, the real local backend and simulator, and a separate process for the real approver CLI. It directly calls `execute_action` before approval and verifies the backend returns `pending_approval` with the simulator still off. This verifies enforcement independently of the model. [Approval integration test](../tests/test_approval_integration.py)
- The CLI approval and execution were recorded before the existing 60-second deadline. Advancing simulator time confirmed one complete five-second pump run. A second execution request returned `lease_consumed` and left the simulator state unchanged. The test verified the audit sequence, `CONSUMED` lease status, exactly one run-ledger row, and exactly one controller receipt for that lease. [Approval integration test](../tests/test_approval_integration.py)
- Stale-state protection passed locally. A version change or changed sensor evidence invalidates an approved lease at execution, including when the reading still meets the level limit. [Lease regression tests](../tests/test_lease.py)
- The November 26 pytest run reported **48 passed and 18 subtests passed**. See the [current stress results](STRESS_TEST.md#observed-results) for the later offline cases.

## Remaining managed-harness checks

- [x] Complete a live approved simulator run in the same chat session before the existing lease deadline and capture the matching audit, run-ledger, and controller-receipt records. [November 27 evidence](evidence/live-integration.json)
- [x] All six managed stress cases passed on November 27. [Sanitized reports](STRESS_TEST.md#observed-results)
- [x] The deployment inspection matched the four expected inline tools and selectors and confirmed shell and file exclusion.
- The inspection reported no guardrail reference. Guardrail attachment and intervention remain unverified. [Deployment inspection](STRESS_TEST.md#deployed-configuration-and-evidence-limits)
- [ ] Verify adjacent inline calls and fragmented SDK event streams against the managed harness. Check tool-result order, matching call IDs, and dispatch after the complete tool handoff. [Read-only handoff probe](STRESS_TEST.md#3-check-live-stream-evidence)

See the [managed-harness stress-test plan](PLAN.md#managed-harness-verification) and [submission priorities](PLAN.md#14-remaining-verification-and-submission-priorities).

## One-terminal integration test

Run from the TwinLock repository root in an interactive PowerShell terminal. For live mode, use the existing AWS credentials, the installed `boto3` version with `invoke_harness` support, and the existing deployment's `TWINLOCK_HARNESS_ARN`. If the ARN is already set, run just the last line. Otherwise, copy the deployed ARN to the clipboard before running the block.

```powershell
if (-not $env:TWINLOCK_HARNESS_ARN) { $env:TWINLOCK_HARNESS_ARN = (Get-Clipboard -Raw).Trim() }
python -m tests.agentcore_smoke --live
```

The test requests the pump run in a fresh session and displays the pending lease from the separate approver CLI. Review the device, action, duration, and time remaining, then type `APPROVE` and press Enter to authorize that lease. The test submits the decision through the approver CLI and immediately requests execution in the same chat session. The original 60-second deadline covers both approval and execution. Press Enter to cancel.

A pass requires one consumed lease, an audit trail showing approval and execution before expiry, one matching run-ledger row, one controller receipt, and an audited refusal of a second execution request for the same lease. The test prints `PASS (live)` or `FAIL (live)`, exits with code `0` or `1`, and prints the path to a JSON report with the observed records. The temporary simulator database is removed after evidence is captured.

For an interactive offline rehearsal, use `python -m tests.agentcore_smoke --offline`. Its fake-harness results are labeled `offline` and saved to a separate report file. The automated offline tests exercise both confirmation and cancellation via `python -m pytest`.

## November 26 manual procedure (historical)

Run these PowerShell commands from the TwinLock repository root. The SDK is required only for the live Python runner.

```powershell
python -m pytest
python -m backend.runner --user user1 --fake
```

Deploy and check the inline handoff from the harness project.

```powershell
$env:AWS_REGION = "us-east-1"
Push-Location .\twinlockagent
agentcore deploy
agentcore invoke --harness twinlock --prompt "List my registered devices."
Pop-Location
```

Copy the deployed harness ARN to your clipboard, then run from the repository root. The direct CLI probe pauses at the inline handoff. The Python runner handles the callback. The live integration uses the [AgentCore inline tools guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-tools.html) and [boto3 API reference](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/invoke_harness.html).

```powershell
python -m pip install --upgrade boto3
$env:TWINLOCK_HARNESS_ARN = (Get-Clipboard).Trim()
$twinlockDb = Join-Path (Get-Location) "db.sqlite3"
python -m backend.runner --user user1 --db $twinlockDb
```

Set the database path in both terminals before requesting the lease. Ask the runner to run `pump-1` for five seconds. Approve the fresh lease in the second terminal, then immediately ask the same chat session to execute it. The deadline starts at proposal and covers both steps.

```powershell
$twinlockDb = Join-Path (Get-Location) "db.sqlite3"
python -m approver.approve --user user1 --db $twinlockDb list
$twinlockLease = Read-Host "Lease ID to approve"
python -m approver.approve --user user1 --db $twinlockDb approve $twinlockLease
```

# Managed harness smoke test

The offline fake tests the local tool round trip. The live boto3 integration uses the published [AgentCore inline tools guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-tools.html) and [boto3 API reference](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/invoke_harness.html).

- [ ] **TODO(verify-in-smoke-test)** Confirm a follow-up invoke accepts the matching tool result as the only content of a user message in the original session. Verify the text content containing the handler's JSON and the order of results when the harness requests adjacent inline tools.
- [ ] **TODO(verify-in-smoke-test)** Check the actual SDK event names and ordering for tool starts, fragmented inputs, block ends, text, and the final message stop. Verify that an inline call is dispatched only after the full stream ends at a tool handoff.
- [ ] **TODO(verify-in-smoke-test)** Verify the exact guardrail attachment key, supported configuration, and availability in us-east-1, then test that the configured guardrail filters a known probe. Guardrail configuration is pending.

## Commands

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

Copy the deployed harness ARN to your clipboard, then run from the repository root. The direct CLI probe pauses at the inline handoff. The Python runner handles the callback.

```powershell
python -m pip install --upgrade boto3
$env:TWINLOCK_HARNESS_ARN = (Get-Clipboard).Trim()
python -m backend.runner --user user1
```

Ask the runner to run `pump-1` for 5 seconds. When the lease is pending, approve it from a second terminal in the repository root and then ask the same chat session to execute that lease.

```powershell
python -m approver.approve --user user1 list
$twinlockLease = Read-Host "Lease ID to approve"
python -m approver.approve --user user1 approve $twinlockLease
```

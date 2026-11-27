# TwinLock rules

TwinLock handles requests for one bounded action on one registered simulator device. The operator selects the session's demo identity, and the runner binds that identity to every tool call.

| Rule | What the user can expect |
|---|---|
| P1 · Device and policy changes | The chat can list devices, read state, propose an action, and execute a lease. The local registration wizard handles device setup. Chat has no device or policy management tool. |
| P2 · Separate consent | A chat message cannot approve a lease. Review the pending device, action, parameters, and time remaining in the separate approver CLI. |
| P3 · Tenant isolation | A session can read its own devices and use its own leases. Another demo user's device data and lease state are outside that session's scope. |
| P4 · Risk and approval | Pump runs require human approval. Door unlocks are critical and also require approval. Custom types default to high risk. A low-risk policy can allow an action with a lease and without a separate approval step. |
| P5 · Limits | A device policy can set stricter duration and lease expiry limits and raise risk within the platform's rules. The platform caps pump runs at 15 seconds, door unlocks at 10 seconds, high-risk leases at 60 seconds, and critical leases at 30 seconds. |
| P6 · Single-use leases | A lease covers the original user, device, action, parameters, and state evidence. It can be consumed once, before its original expiry. |
| P7 · Execution checks | The backend re-checks the action, current state and cumulative budget before consumption. Approval does not reserve an exception to any of these checks. |
| P8 · Untrusted text | Device notes, names, telemetry text and chat are untrusted data. Statements in that data cannot grant approval, change policy, or add tool capabilities. |
| P9 · Uncertain state | Missing, stale, or conflicting telemetry needed by policy yields a refusal or inconclusive result. A change to bound state can invalidate an approved lease. |
| P10 · Independent controller limits | The simulator controller checks the command token, supported action and duration. It also enforces the pump interlock, pump heartbeat and timed door relock. |

The demo pump additionally has a level precondition and a cumulative runtime budget. The configured per-user quotas limit registrations, pending leases, and proposal attempts. The runner bounds chat turns and tool calls. [Configured limits](../backend/config.py) · [Demo manifests](../backend/demo.py)

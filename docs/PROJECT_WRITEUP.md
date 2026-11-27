# TwinLock: Giving an AI agent a bounded action

An AI agent can understand “run the pump for five seconds.” Understanding the request gives it no basis for deciding whether the user owns the pump, whether the tank has room, or whether a human consented to that particular run. TwinLock explores that gap with a local software simulator and an agent that can propose actions through a small, checked interface.

## Watch the Demo

The video demonstrates the offline browser simulator. Live AWS AgentCore verification was performed separately through managed stress and smoke tests. [Watch the TwinLock demo on YouTube](https://youtu.be/c4D_zpJl55Y) · [Test results](#what-the-tests-showed)

![Offline browser simulator showing an execution attempt waiting for human approval while the pump stays idle](images/blocked-execution.png)

## A request becomes a lease

The browser sends a chat turn to a Python runner. The runner binds the session to one of two demo identities and passes the turn to an offline scripted harness or an existing AWS AgentCore managed harness. The harness can request four inline tools. The runner validates each tool name and argument schema before any dispatch and supplies identity from its own session map.

The policy engine checks ownership, manifest limits, state freshness, and budget. A request that passes those checks receives a lease scoped to a device, action, parameters, expiry, and state evidence. For the simulated pump, the lease initially waits for a human to review and approve it through a separate CLI. When the agent requests execution, the backend checks the lease again and consumes it atomically. The simulator controller redeems that command once and enforces its own hard limits. The browser renders current simulator state and verified tool outcomes, while SQLite records the lifecycle. [Architecture diagram](../README.md#architecture)

![Architecture of the managed AgentCore harness and local enforcement, including the separate approver CLI and independent simulator controller](images/architecture.png)

## Enforcement mapping

| Rule | Enforcement |
|---|---|
| Capabilities and tenant boundaries | The four-tool dispatcher rejects unsupported tools and caller-supplied identity. Device, lease, and audit views use the server-bound tenant. |
| Independent approval | Only the separate local approver changes the approval state. The approval-required pump lease keeps its original 60-second deadline. |
| Bounded execution | Risk floors, parameter bounds, state checks, and budgets run at the appropriate proposal and execution steps. |
| Expiry and replay | A conditional SQLite update consumes the valid lease. Controller redemption also requires a matching command and a unique receipt. |
| Independent simulator safeguards | The controller enforces its own action limits, pump interlock and heartbeat, and timed door relock. |
| Untrusted text | Chat and device metadata carry data. Authorization comes from checked tool calls and persisted lease state. |

The complete rule list includes how the engine handles uncertain telemetry and how the audit distinguishes backend consumption from a controller outcome. [Rules](RULES.md) · [Enforcement details](ENFORCEMENT.md)

## What the tests showed

The offline suite passed 61 tests and 18 subtests. The separate ablation ran 12 scripted cases across four configurations, for 48 cells with zero unexpected results. For example, without backend policy enforcement the controller still blocks an oversize run, while the ablation shows why human approval has to be enforced by the lease path. [Offline pytest summary](evidence/offline-pytest.json) · [Ablation results](ABLATION.md)

The November 27, 2026, managed run passed all six stress cases. A cross-tenant read returned `device_not_found`. An unapproved execution remained pending, and an expired attempt returned `lease_expired`. The injection and restricted-tool prompts produced no unauthorized local effect. Replay returned `lease_consumed`. In the live smoke test, one approved simulator execution produced one matching ledger row and one controller receipt. [Case-by-case results](STRESS_TEST.md#observed-results) · [Sanitized smoke report](evidence/live-integration.json)

![Archived live AWS AgentCore results showing all six managed stress cases passed](images/live-stress-results.png)

## Challenges and lessons

An early managed approval attempt missed the expiry window. The successful rerun used the same deadline and recorded approval, consumption, and replay in order. The ablation initially treated a successfully created pending lease as an approval refusal. Checking the execute outcome separately made that distinction visible in the matrix. These runs showed why the demo should show both the decision and what the simulator actually did. [Smoke history](SMOKE_TEST_TODO.md) · [Ablation findings](ABLATION.md#what-broke-and-how-it-was-fixed)

TwinLock runs locally with trusted demo identities and software devices. It has no production authentication, public hosting, or hardware-safety validation. The deployed configuration inspection found no Bedrock Guardrail reference. Attachment or intervention, live stream fragmentation, and the browser's live AWS path remain unverified. [Evidence limits](STRESS_TEST.md#deployed-configuration-and-evidence-limits) · [Local demo](../README.md#run-locally)

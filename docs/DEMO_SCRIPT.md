# TwinLock in 90 seconds

Start the offline browser demo with a fresh local database. Open a second PowerShell terminal at the same repository root before recording. Keep the approval command ready and the browser set to `user1`. [Startup and approval commands](../README.md#run-locally)

| Time | Show | Say |
|---|---|---|
| 0–12s | Software simulator badge, workflow, and archived counters | “TwinLock lets an AI agent request a bounded action. Independent code checks the request, and high-risk actions require a separate human decision.” |
| 12–26s | Click **Propose demo action**. Point to the pending lease and countdown. | “The pump run is scoped to this user, device, and duration. The lease expires on its original deadline.” |
| 26–39s | Click **Execute existing lease** before approval. Show the idle pump and audit. | “The backend leaves the request pending. The pump stays idle.” |
| 39–56s | In the second terminal, review the CLI listing and approve only if you choose. | “I review the action and authorize this one lease through the separate approver.” |
| 56–69s | Return to the browser and execute the approved lease. Show state and audit. | “The controller runs the simulated pump within its limits. The execution consumes the lease.” |
| 69–81s | Request execution again. Show the replay refusal and then switch to `user2`. | “Replay is refused. The second demo identity sees only its own hall light.” |
| 81–90s | Point to historical verification and the repository README. | “This recording is offline. The archived reports cover the offline suite, ablation matrix, and six managed stress checks.” |

If the lease expires before you authorize and execute it, restart with a fresh demo database and record a new attempt. Keep the private terminal path and full lease identifier out of a public recording.

# Project rules for coding agents

Project: TwinLock. Read docs/PLAN.md before any change. Do not expand scope beyond PLAN.md Section 3.
Demo users are user1 and user2 (not Alice/Bob).

1. Do NOT run git commit, git push, or any command outside this project folder unless asked.
2. Never print, log, or store AWS keys, secrets, or the AWS account ID. Never put secrets in tracked files.
3. Do NOT call AWS in unit tests. AWS calls are allowed only in backend/runner.py and attacks/run_ablation.py.
4. No agent-facing tool parameter may carry user_id. Identity comes from the runner's session map.
5. Enforcement must be deterministic code, never prompt text alone.
6. All limits, TTLs, and ceilings live in backend/config.py. No hard-coded numbers elsewhere.
7. Use Python standard library plus pytest only, unless you explain why a dependency is needed.
8. After each milestone: run pytest, summarise results and any failures, then STOP and wait for review.
9. If you are unsure about an AWS API field or config key, do not guess. Leave a TODO and list it in your summary.
10. Keep functions short, typed, and tested. One small change at a time.
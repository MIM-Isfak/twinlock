# TwinLock publication checklist

## Repository and evidence

- [ ] Review the intended Git diff and include the application, tests, MIT license, documentation, sanitized evidence, and README screenshots.
- [ ] Run the offline pytest and ablation commands from a fresh checkout. Confirm the results match the checked-in summaries. [Commands and expected results](../README.md#verified-results)
- [ ] Verify local setup starts the loopback-only dashboard and the approver CLI uses the same local database as the browser demo.
- [ ] Check tracked and untracked publishable files and reachable Git history for credentials, AWS account or deployment identifiers, and temporary database files. Keep local deployment configuration, virtual environments, browser profiles, and scratch databases ignored.
- [ ] Open the README links and check the two screenshots at desktop and mobile sizes. Crop terminals, full lease IDs, and local paths from any additional images or video.
- [ ] Label archived live results separately from browser state. Keep guardrail attachment and intervention, wire fragmentation, and browser AWS integration marked unverified.

## Portfolio release

- [ ] Review the [project article](PROJECT_WRITEUP.md) and rehearse the [90-second script](DEMO_SCRIPT.md).
- [ ] Review the staged files before committing or making the repository public. Keep the browser app local and use screenshots or a recording to show it.
- [ ] Publish the repository and article only with the owner's authorization. Present TwinLock as an independent technical portfolio project.

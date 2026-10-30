# Deterministic execution and recovery

The skill is the agent's decision procedure. `scripts/relay.py` is ordinary Python and has no inference dependency. The first browser bridge uses Chrome's supported AppleScript interface on macOS. It addresses a newly created window/tab by ID, inspects only that tab, fills its visible composer and clicks an unambiguous Send control. It neither reads browser profiles nor calls private model endpoints. The current composer selector includes visible `contenteditable[role=textbox]`; website selectors remain version-sensitive.

## Process ownership

`start` exclusively creates a job directory, copies the packet and launches a detached, task-scoped supervisor. Returning the receipt releases the main agent from monitoring. The supervisor launches a separate worker process group. It counts attempts durably, detects a 45-second stale heartbeat, applies bounded backoff and enforces the original absolute deadline. The supervisor can terminate only the child process group it launched. Chrome is an existing user application and is never terminated.

The supervisor holds `supervisor.lock`. Each worker holds `worker.lock`. State writes hold `state.lock` and verify an ownership token; a replacement supervisor changes that token so an old in-flight browser operation cannot overwrite a newer outcome. Workers also receive the expected supervisor PID, rather than adopting whatever parent remains after a crash. Successful and exceptional browser paths both preserve ownership checks.

If the supervisor itself dies, the worker notices the parent change and exits after any bounded in-flight browser operation. A later `status` reports interrupted. `resume` obtains the supervisor lock, changes ownership and retains the original budget. There is no auto-start service, reboot guarantee or model-powered watcher.

## Durable job files

One directory owns one job; there is no shared database or migration. Runtime data is excluded from Git. The CLI uses private file permissions, atomic replacement and file fsync for committed content. This is process-crash recovery, not a guarantee against filesystem corruption or sudden hardware failure.

| File | Consumer and content |
| --- | --- |
| `prompt.md` | Worker: minimal authorized task packet |
| `status.json` | Supervisor, worker and collection CLI: job ID, phase, timestamps, absolute deadline, attempts/max attempts, polling interval, configured URL, owner token, task tab IDs, recorded conversation URL and terminal reason |
| `result.txt` | Agent: raw answer saved before successful terminal state |
| `partial.txt` | User/agent diagnosis: incomplete observed answer, never a successful result |
| `event.json` | Optional host integration: stable terminal outcome and result path; reconstructable from persisted terminal state if interrupted before emission |
| `cancel` | Supervisor/worker: user cancellation request |
| `worker.log` | Local diagnosis: worker errors; not a published artifact |

`queued → submitting → waiting → completed` is the normal path. `needs_attention`, `timed_out` and `cancelled` are other terminal outcomes. `interrupted` is a read-time observation of missing supervision, not an excuse to reset a job. Terminal state cannot be overwritten by a stale worker. Re-reading a terminal job repairs a missing event without rewriting an existing event.

## External side effect boundary

Persist `submitting` before Send. On any restart in submitting/waiting, only inspect the original task tab or a recorded conversation URL. A unique user-message marker and final answer marker correlate output to the job. Require the expected origin and absence of generation controls before accepting the final marker. A tab redirected to another origin receives no packet. Local synthetic tests are restricted to their configured loopback origin too.

If the worker dies before clicking, it conservatively leaves an ambiguous submission rather than assuming another attempt is safe. If it dies after clicking, the same durable intent prevents a second submission. This favors a visible failure over duplicates; the website does not provide an exactly-once transactional primitive.

## Completion and limits

The worker saves raw output and a terminal event. This repository does not install a host-specific callback and does not claim that a file can wake Codex. An agent may attach an existing authorized completion facility when its host provides one; otherwise collection is a later explicit interaction. Login/challenges and platform limits need human action. Provider permission remains separate from browser capability and account access.

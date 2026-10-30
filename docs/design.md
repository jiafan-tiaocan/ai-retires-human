# Deterministic execution and recovery

The skill is the agent's decision procedure. `scripts/relay.py` is ordinary Python and has no inference dependency. New jobs default to Ego Lite's official `ego-browser nodejs` runtime. Each job owns one durable TaskSpace, named from the job ID, and resumes its numeric space ID and Page label on every worker operation. An explicitly supplied space ID reuses the current user goal's agent-owned space and must not serve concurrent jobs. The runtime injects its documented Page API; this backend neither imports Playwright nor inspects profiles. It checks local owner/deadline/cancellation immediately before native fill/send/refresh operations. User handoff/control loss ends the job for attention; it never claims a user-owned space. After successful collection/validation the agent finishes the TaskSpace through the vendor API; failures retain it for diagnosis. CLI console output can arrive on stderr, so receipts are extracted from both output streams.

The optional `--browser existing` path uses Playwright's native CDP connection to already-running Chrome 144+. A local private Unix-socket broker retains one authorized connection across jobs and worker restarts. It preserves context defaults with `no_defaults=True`, and opens only task-owned pages. `session-start` establishes the connection and `session-stop` independently requests shutdown; individual workers do not disconnect it. The broker verifies request ownership, deadline, cancellation and live client before mutations, and an independent watchdog stops a stalled connector. It disconnects its own transport without closing the user's browser/context. No credentials are copied. Chrome can require a new grant when the connector restarts.

All backends use origin-bound DOM observations, visible composer input and an unambiguous native Send action. The legacy AppleScript bridge and owned-browser modes remain compatibility/testing paths. Website selectors remain version-sensitive. There are no private model endpoints.

The optional `connect` command launches an owned Chromium profile, waits without inference while the user logs in, and releases the profile when the user closes the window. Google can reject sign-in in that automated context; a normally authenticated Ego Lite or existing Chrome session avoids repeating that login. Owned profiles have a lock and can be isolated for tests. The browser manages its own authentication storage.

## Process ownership

`start` exclusively creates a job directory, copies the packet and launches a detached, task-scoped supervisor. Returning the receipt releases the main agent from monitoring. The supervisor launches a separate worker process group. It counts attempts durably, detects a 45-second stale heartbeat, applies bounded backoff and enforces the original absolute deadline. The supervisor can terminate only the child process group it launched. Group cleanup checks surviving group members even after the worker leader exits and escalates after a grace period. Playwright manages its owned browser processes; actual browser restart tests are required to establish that a profile can be reused after a crash. The legacy bridge leaves the user's pre-existing Chrome untouched.

The supervisor holds `supervisor.lock`. Each worker holds `worker.lock`. State writes hold `state.lock` and verify an ownership token; a replacement supervisor changes that token so an old in-flight browser operation cannot overwrite a newer outcome. Workers also receive the expected supervisor PID, rather than adopting whatever parent remains after a crash. Successful and exceptional browser paths both preserve ownership checks. The borrowed Chrome is outside the worker's process group and is not terminated by worker cleanup.

If the supervisor itself dies, the worker notices the parent change and closes its own browser after any bounded in-flight operation. A later `status` reports interrupted. `resume` obtains the supervisor lock, changes ownership and retains the original budget. A replacement owned-Playwright worker starts its browser again and opens the recorded conversation URL. It never treats an old in-memory page handle as recoverable or submits again when the conversation URL is uncertain. There is no auto-start service, reboot guarantee or model-powered watcher.

## Durable job files

One directory owns one job; there is no shared database or migration. Runtime data is excluded from Git. The CLI uses private file permissions, atomic replacement and file fsync for committed content. This is process-crash recovery, not a guarantee against filesystem corruption or sudden hardware failure.

| File | Consumer and content |
| --- | --- |
| `prompt.md` | Worker: minimal authorized task packet |
| `ego-page.json` | Ego backend: durable space/Page allocation saved before navigation, so retries retain the allocated page |
| `status.json` | Supervisor, worker and collection CLI: job ID, phase, timestamps, absolute deadline, attempts/max attempts, polling interval, configured URL, owner token, task tab IDs, recorded conversation URL and terminal reason |
| `result.txt` | Agent: raw answer saved before successful terminal state |
| `partial.txt` | User/agent diagnosis: incomplete observed answer, never a successful result |
| `event.json` | Optional host integration: stable terminal outcome and result path; reconstructable from persisted terminal state if interrupted before emission |
| `cancel` | Supervisor/worker: user cancellation request |
| `worker.log` | Local diagnosis: worker errors; not a published artifact |

`queued → submitting → waiting → completed` is the normal path. `needs_attention`, `timed_out` and `cancelled` are other terminal outcomes. `interrupted` is a read-time observation of missing supervision, not an excuse to reset a job. Terminal state cannot be overwritten by a stale worker. Re-reading a terminal job repairs a missing event without rewriting an existing event.

## External side effect boundary

Persist `submitting` before Send. On any restart in submitting/waiting, only inspect the original task tab or a recorded conversation URL. A unique user-message marker and final answer marker correlate output to the job. Require the expected origin and absence of generation controls before accepting the final marker. A tab redirected to another origin receives no packet. Local synthetic tests are restricted to their configured loopback origin too.

If the worker dies before clicking, it conservatively leaves an ambiguous submission rather than assuming another attempt is safe. If it dies after clicking, the same durable intent prevents a second submission. Only an explicit known provider rejection with no answer and an instruction to resend permits one refresh/retry, consuming the original persisted attempt budget. This favors a visible failure over duplicates; the website does not provide an exactly-once transactional primitive.

## Completion and limits

The worker saves raw output and a terminal event. This repository does not install a host-specific callback and does not claim that a file can wake Codex. An agent may attach an existing authorized completion facility when its host provides one; otherwise collection is a later explicit interaction. Login/challenges and platform limits need human action. Provider permission remains separate from browser capability and account access.

Cancellation cannot recall an external browser action already dispatched. Ambiguous native action receipts retain submitting/waiting and never authorize an automatic duplicate.

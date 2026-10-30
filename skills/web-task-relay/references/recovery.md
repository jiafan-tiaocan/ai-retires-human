# Recovery

Use one `status JOB_DIR` call to diagnose a user-reported failure, rather than an agent polling loop. Status records and outputs are local to the job; do not publish them by default.

| Status | Meaning and next action |
| --- | --- |
| queued | Worker has not reached submission. Its supervisor owns bounded retry. |
| submitting | Submission intent was persisted. The click may or may not have occurred. Never create a replacement job automatically. |
| waiting | The worker observes the original tab/conversation. It does not submit again. |
| completed | `result.txt` and `event.json` are available. Verify task correctness. |
| needs_attention | Read `reason`. Login, challenges, changed selectors and ambiguous submission require attention. Inspect the original conversation before deciding to start another task. |
| timed_out | The original absolute deadline elapsed. Partial output and conversation references remain available. No automatic fresh budget or resend. |
| cancelled | Local execution stopped. The remote model might still be generating; remote conversation is preserved. |
| interrupted | The supervisor is absent. `resume JOB_DIR` keeps the same attempt count and deadline, and conservatively recovers the original conversation. It does not promise unattended reboot recovery. |

`cancel JOB_DIR` requests cancellation; the supervisor terminates only its owned worker. It does not close Chrome, delete chats or clear storage.

Default jobs use Ego Lite's official CLI. Their TaskSpace and Page label survive worker restarts. Resume the same space; do not create a new one to get around an error, user handoff or login challenge. Successful verified collection ends the TaskSpace through the official API; errors retain it. `--ego-space ID` is for reusing this goal's agent-owned space, not for operating unrelated user spaces. Never run concurrent jobs in the same space.

With `--browser existing`, `session-start` retains one Chrome connection across jobs and worker crashes. `session-stop` disconnects only that connector. A reconnect can request Chrome permission again; the runner does not suppress it. A restarted observer recovers the original task page or recorded conversation URL, preserving the no-resend boundary.

Optional `--browser chromium` or `--browser firefox` launches an owned dedicated profile. Close its login window before dispatching a job; a busy profile is retried within the job budget. Google SSO can reject these automated contexts. The legacy `--browser apple-events` mode is retained only for earlier macOS jobs; it is not the default. DOM selectors can still change. The runner does not switch to private endpoints, copy cookies or use anti-detection techniques. A missing completion marker is incomplete transport, not success. A machine reboot or killed supervisor requires explicit resume.

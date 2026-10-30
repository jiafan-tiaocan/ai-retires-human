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

The first runner uses a Mac-specific AppleScript bridge and DOM selectors which can break when the website changes. It does not silently switch to private network endpoints or anti-detection techniques. A missing completion marker is an incomplete transport, not success. The task-scoped supervisor can restart a crashed worker within the persisted budget; a machine reboot or killed supervisor requires explicit resume.

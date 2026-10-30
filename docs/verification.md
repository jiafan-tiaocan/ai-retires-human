# Verification — 2026-10-30

**The default Ego Lite backend completed a real ChatGPT send → wait → collect journey, including recovery after an injected worker crash.** This is a working macOS candidate. Provider policy permission, portability and automatic host wakeup are separate, unestablished claims.

Runner SHA-256: `31756f01352418eaaacfb2f32a1b81c0525ea750f3094e8cb1f6931c51a16190`. Environment: macOS, Python 3.9, Ego Lite 0.6.2.5, official ego-browser guide v2.2.0. The page's model selector displayed Pro; no backend model ID is asserted.

## Real provider acceptance

The public [planning packet](../examples/plan-comparison.md) supplies four fictional collaboration options for an eight-person team with an 800 yuan monthly budget, Chinese support, Markdown export and at least 90 days of history. It requests a recommendation, exclusions, risks, validation and a pilot plan. It requests no coding or implementation.

| Observation | Result |
| --- | --- |
| Normal automatic submission, waiting and collection | Completed on the first attempt; no manual fill/send/recovery |
| Final runner, worker deliberately killed after persisted waiting | Completed on attempt 2 in 113.65 seconds, within the original deadline |
| Recovery page | Same persisted TaskSpace/Page label; no replacement task or manual intervention |
| Returned selection and monthly cost | B; 8 × 90 = 720 yuan |
| Exclusions | A: only 30 days history; C: no Markdown export; D: 880 yuan exceeds budget |
| Deliverables | Two risks, three validation steps, three pilot steps; locally checked against supplied inputs |
| Message correlation | Matching visible user marker and assistant completion marker; no active generation control |

The provider page kept a hidden copy of the user bubble as well as the visible bubble. Counting all matching DOM elements would incorrectly imply two messages; there was one visible matching user bubble. This is UI evidence, not a server-side exactly-once guarantee. The local synthetic server separately records submission counts.

The source-controlled packet is public fictional data. Account information, conversation URLs, raw page text and local job receipts remain in ignored private runtime storage. Raw transport text may include visible UI labels; collection extracts the requested payload and validates it. The terminal marker proves transport correlation, not correctness.

The main agent used code-based waits while testing and continued implementation/review. The shipped worker and supervisor make no model calls. No percentage token saving was measured.

## Local checks and independent review

- Skill Creator validator: passed.
- Ruff 0.14.4, E9/F over skills/tests, and Git whitespace check: passed.
- 31 logic tests: passed, including the generated Ego script executed in real Node for navigation between target selection and native fill/send. On hosts without Node, that one regression is explicitly skipped; set `RELAY_TEST_NODE` to an available Node binary to run it.
- Real Chrome protocol/local-server fixtures: normal, worker crash, supervisor crash, borrowed browser, and shared-connector worker crash passed. The server saw one submission per task. Owned fixtures clean up only their own resources; borrowed browser remains usable.
- Real Ego Lite/local-server fixture: contenteditable input, worker killed after submission, completed on attempt 2; the server recorded exactly one submission.
- Independent read-only implementation review: passed after fixes. The reviewer independently re-ran 31 tests, used a local SDK stub to allocate two jobs to distinct pages in one TaskSpace, verify retry reuse of the first allocation and preserve the original page. It did not use the real account.
- Independent skill-routing evaluation: a complete planning comparison can dispatch; coding/debugging/test/deployment requests stay local; explanatory pseudocode remains design work; vague goals require local clarification; no saving request means no automatic web handoff.

Coverage includes ambiguous sends, bounded retry/deadline persistence, stale-owner writes, terminal-event repair, process-group cleanup after leader death, hung worker/connector shutdown, disconnected IPC clients, cancellation, user-control stops and page-origin changes. Native Ego actions target a nonce on a specific original-document element, so navigation cannot redirect the packet into a new page's generic composer. Allocation is recorded before navigation and reused after crashes.

## Reproduction

Install developer dependencies as shown in the README, then:

```sh
.venv/bin/ruff check --select E9,F skills tests
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python tests/browser_fixture.py
```

To test the real Ego browser with synthetic loopback data, first create/resume the current goal's agent-owned TaskSpace through its official API. Pass that numeric ID; the fixture opens its own page within the space:

```sh
.venv/bin/python tests/browser_fixture.py --scenario egolite-worker-crash --ego-space NUMERIC_ID
```

The fixture invokes no model. A real-provider test is opt-in: normally sign in, dispatch the example packet, collect, then independently validate the constraints. End the successful TaskSpace through the official `finish({keep: []})` API. Preserve error spaces for diagnosis and yield when the user takes control.

## Remaining limits and historical failures

Earlier Apple Events runs failed on permission state; a dedicated automated Chrome login was rejected by Google; Firefox did not launch on the tested host. These are historical alternatives, not prerequisites for the successful Ego backend. Chrome native attachment remains optional and may request a fresh grant when its connector restarts.

No unattended machine-reboot recovery, automatic Codex wakeup, Linux support, independent/free quota pool, unlimited usage, stable future website selectors or savings percentage was demonstrated. Windows is unsupported by the POSIX runner. A later explicit collect/resume uses persisted state; the project installs no OS service or recurring model-powered watcher.

Technical success does not establish provider permission. OpenAI's [Terms of Use](https://openai.com/policies/terms-of-use/) restrict automatic extraction of output; this project has not established an exception. Browser/account access and third-party browser claims do not change that limitation.

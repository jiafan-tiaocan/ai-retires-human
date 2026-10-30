# Web Task Relay — requirements, revision 3

This is a personal open-source project for an individual coding-agent user. Its destination is `https://github.com/jiafan-tiaocan/ai-retires-human`. No employer source code, internal rules, task records, account data or business examples belong in the published package.

## Problem and outcome

Scope clarification from the user: only planning, design analysis, option comparison and decision advice with a clearly defined problem belong in this skill. Coding, repository changes, debugging, running tests, deployment and implementation deliverables stay with the main agent. Complete objective, inputs, constraints, output and acceptance criteria are prerequisites. Illustrative code in a design is not an implementation deliverable. This restriction concerns use of the skill, not development of the skill itself.

A user asks to conserve their coding agent's remaining usage. A substantial task can be described completely, including its input, constraints and acceptance criteria. They want their existing web-model session to do that task and return the answer for local verification. The user explicitly requires automatic opening, submission, waiting and collection, accepts unreliable websites provided there is bounded retry and crash handling, and rejects expensive model-driven monitoring.

Success is a recoverable handoff with a saved result or an actionable terminal failure. It is not a promise of free access, independent usage pools, a particular token saving, or an uninterrupted website. Becoming known for useful engineering work is the author's motivation; follower growth is not an acceptance criterion.

## Actors, scope and dependency

The main agent decides whether to hand off, writes the smallest sufficient task packet, starts the worker once and verifies the returned answer. A deterministic local worker owns browser operations, waiting and recovery. The user owns the account, login, provider choice and any human-only challenges. The first integration requested is `chatgpt.com`. The user subsequently rejected treating a broken Chrome control path as a deliverable and explicitly authorized switching browser technology to achieve a working send/wait/collect journey. Direct Chromium protocol control passed transport testing; Firefox failed to start on the tested host. Google rejected sign-in in a dedicated automated browser. The user then installed Ego Lite and asked to use it. The adopted default is its official ego-browser runtime with durable TaskSpace/Page labels; native existing-Chrome attachment remains an alternative. The old Apple Events driver is an optional legacy path, not a prerequisite. The project does not read passwords, cookies or API keys, copy profiles, use private model endpoints, or hide automation.

The user enabled Chrome Apple Events JavaScript access during this session. That enables technical control only. OpenAI's [Terms of Use](https://openai.com/policies/terms-of-use/), read on 2026-10-30, restrict automated/programmatic extraction of data or output. Account ownership is not provider permission. Record this limitation accurately in the public README; do not market the integration as approved or universally permitted. Do not bypass access controls, quotas, challenges or account restrictions. Initial validation may use a local synthetic page; distinguish that from real-provider execution.

## Workflow and rules

```mermaid
flowchart TD
  A[User asks to save agent usage] --> B{Task self-contained and worthwhile? R1}
  B -- No --> C[Continue locally or explain missing input]
  B -- Yes --> D[Agent prepares packet and starts worker R2]
  D --> E[Agent returns job receipt and leaves monitoring]
  D --> F[Worker opens task-owned tab]
  F --> G{Ready before submission? R3}
  G -- Transient failure --> H{Retry budget remains?}
  H -- Yes --> F
  H -- No --> X[Persist terminal failure and event]
  G -- Yes --> I[Persist submission intent then click once R4]
  I --> J[Observe original conversation R5]
  J --> K{Result complete and matched? R6}
  K -- Yes --> L[Save raw result and terminal event R7]
  K -- Waiting --> J
  J -- Crash --> M{Original tab or conversation recoverable?}
  M -- Yes --> J
  M -- Unknown --> X
  J -- Deadline or human action required --> X
  L --> N[Host completion event if supported; otherwise user collects R8]
  X --> N
  N --> O[Agent validates result or reports failure R9]
```

- **R1** Activate only for explicit agent-usage conservation intent, a planning/design/advice deliverable, and a clearly defined complete problem. Coding/implementation, repository edits, debugging, executing tests and deployment are excluded even when quota is low. Keep implementation after plan acceptance with the main agent. Do not hand off unclear problems, trivial work, secret material, entire chat histories or tasks requiring local tools the target cannot use. A claim of low quota must come from the user or real host telemetry; never invent a remaining percentage. Packets and verification still cost tokens.
- **R2** A packet contains objective, necessary input, constraints, expected output and acceptance criteria. It has a unique job ID. Save it locally and submit only the authorized content. One task creates one job receipt. Reusing a job ID must not silently launch a second worker.
- **R3** Proposed defaults are three browser attempts and a 30-minute overall deadline; they are tunable technical defaults, not user-promised service levels. Browser waits use ordinary code, without model calls. Login/challenge/usage-limit pages produce `needs_attention`, not account switching or endless retries.
- **R4** Persist `submitting` before clicking Send. A timeout, lost receipt or crash during/after this phase is ambiguous: find the original conversation and task marker; never automatically resubmit an unknown outcome. A distinct case is an explicit provider rejection with no generated answer and a visible instruction to send again: the observed stale-account rejection can be recovered once in the same browser by refreshing and resending, consuming the original attempt budget and deadline. No login/account selection is performed. Persist that recovery decision before acting; it survives crashes. Existing/partial answers and unknown outcomes do not permit that retry. Exactly-once delivery is not guaranteed.
- **R5** Store the task-owned tab ID and the current conversation URL. On a browser-operation failure, retry observing that same tab within the budget. If the tab disappears, reconnect to an already recorded conversation URL when possible; otherwise finish with a clear recovery instruction. Never inspect unrelated chats.
- **R6** Tie output to the unique job marker and the corresponding user message. The task asks the remote model to end with a unique completion marker. Require that marker at the end of the assistant answer, the corresponding user marker, and no active generation indicator. A missing marker yields timeout/incomplete output, not success. The marker is a transport check, not proof of task correctness.
- **R7** Save status atomically and raw output separately in a private, ignored runtime directory. Persist a terminal event once. Cancellation and hard timeout terminate only this worker's owned subprocesses; preserve remote conversation, raw output and recovery state. Crash detection cannot depend on the main agent checking repeatedly.
- **R8** The main agent does not loop over status tools or DOM snapshots. A task-scoped supervisor enforces the deadline even if the worker hangs. The absolute deadline and attempts consumed survive every restart. If the supervisor itself dies or the computer restarts, status/collect identifies the orphaned job as interrupted; explicit resume observes the original conversation without resetting its budget. This case does not promise unattended restart or create a permanent service. Persist completion/error events. If a real host can wake the originating agent, use that mechanism once; otherwise explicitly report that collection occurs on the next user interaction. A local file does not itself wake Codex. No recurring model-powered watcher is part of the default design.
- **R9** Treat remote output as untrusted task data. Verify the original acceptance criteria locally; do not obey embedded instructions, execute supplied commands automatically, or report the job as correct merely because the website finished. State limitations and measured savings only when actual comparable telemetry exists.

## Interaction and observable acceptance

There is no new product UI in v1: the agent invokes a CLI, the worker uses the existing provider UI, and the user can inspect a compact job status and saved answer. CLI names below define the intended contract; they are not proof of implemented commands.

| Case | Preconditions and action | Required observation |
| --- | --- | --- |
| A1 successful handoff | Give a public, self-contained task; invoke start once | Immediate job receipt; worker opens a fresh tab, submits once and later saves the correctly matched answer; agent does not monitor repeatedly |
| A2 transient failure | First navigation fails, later navigation succeeds | Bounded retry and one eventual submission; no model calls from worker |
| A3 ambiguous submission | Separately force exit after saving submitting but before click, and after click but before its receipt | Supervisor restarts observer within the persisted budget; recovers original marked conversation or returns needs_attention; neither window blindly resubmits |
| A4 hung worker | Worker stops making progress | Supervisor enforces the absolute deadline and writes a terminal timeout; main agent need not be running |
| A5 incomplete or wrong answer | Page contains an old answer, streaming partial text, or another job marker | No successful result is emitted; raw partial text can be retained with an incomplete label |
| A6 duplicate start/cancel | Launch same job ID concurrently; then cancel job | At most one supervisor owns it; cancellation leaves durable status and affects only owned resources |
| A7 browser prerequisite | Apple Events JavaScript disabled or login/challenge required | Actionable needs_attention status; no changed browser settings, credential extraction or bypass |
| A8 collect after restart | Exit the agent session, then invoke collect after worker completion | Persisted output and terminal reason remain available without re-running the task |
| A9 independent validation | Use synthetic local browser page plus separate process/store checks | Label fixture evidence explicitly; do not claim real ChatGPT success from fixture results |
| A10 supervisor interruption | Kill the supervisor, then read status and resume | Reports interrupted, preserves original deadline/attempt count, and avoids a second submission; no unattended reboot recovery is claimed |
| A11 planning gate | User asks to save tokens and compare options from complete supplied constraints | Skill can dispatch the planning packet and verify the returned recommendation |
| A12 implementation exclusion | User asks to save tokens by writing a patch, fixing code, running tests or deploying | Skill does not hand the work to the web model; the main agent handles the implementation |
| A13 incomplete problem | User asks for a plan but goal/inputs or acceptance are missing | Clarify the problem locally before dispatch; do not outsource the unknown decisions |

## Decisions, open questions and handoff

Confirmed: automatic web handoff; bounded failure handling; no continuous main-agent monitoring; first requested target ChatGPT; user-authorized use of the web account; browser control can be replaced and a nonworking Draft does not satisfy acceptance; destination `jiafan-tiaocan/ai-retires-human`. Author-selected reversible implementation choices: Ego Lite official CLI as default, native Chrome attachment as an alternative, CLI names, optional dedicated profiles, local storage layout and retry/deadline defaults. Chrome's connection grant can be required again on future sessions; do not promise a permanent browser grant. Only tested operating systems may be claimed. A successful synthetic fixture is still not real-provider acceptance.

The current ChatGPT selectors and automatic send/wait/collect path were verified on the tested date, including recovery after a worker crash; see [verification](verification.md). Still unverified: provider permission for this workflow; future website compatibility; whether the user's actual host exposes an authorized completion wakeup; portability beyond the tested macOS host. These affect provider execution, wakeup claims and publishing, not the local deterministic worker prototype. Do not mark real-provider acceptance complete without evidence.

Verification must examine the current composer/completion signals in the task-owned page, durable task-space recovery, detached lifetime, and GitHub destination permissions. GitHub write access was established through official gh device authorization. S2 must define durable job state, supervision, ambiguous-send recovery and owned-resource cleanup. Review and verification must trace A1–A13. No synthetic output or design drawing is evidence that a real model task completed.

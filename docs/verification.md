# Verification record — 2026-10-30

Status: **experimental review candidate; real-provider acceptance incomplete**.

The candidate's browser runner SHA-256 is `2a84c4f1068c5d9c3dac29136b7f6c8dc652ce5c391ac476203d2f4f98523169`. Recompute this when changing the runner; do not transfer these results to a different implementation without checking the relevant changes.

## Passed locally

- Skill Creator's frontmatter/name/scaffold validator: passed.
- Ruff 0.14.4, rules E9/F, over `skills` and `tests`: passed.
- `python3 -m unittest discover -s tests -p 'test_*.py' -v`: **16 tests passed** on macOS/Python 3.9.
- An independent reviewer re-ran the same tests and reviewed the final corrections.

The cases exercise one successful marked transport using a fake browser, stale/wrong result rejection, a crash before clicking Send, a lost receipt after submission, duplicate job rejection, supervisor locking, preserved retry/deadline budgets, late stale-worker errors, terminal-event repair, origin changes, and a real hung subprocess killed at the overall deadline. The actual generated probe/fill/send JavaScript was executed in macOS JavaScriptCore to verify that a wrong origin is rejected before DOM access. These are local logic/runtime checks, not model-quality evidence.

Independent review exposed three defects which were corrected and retested: a stale worker could overwrite a newer terminal state; an interrupted terminal-state commit could lose its event; and checking origin only in a prior snapshot left a navigation window before fill/send. The final code protects writes with job ownership, repairs missing events and checks origin inside each page operation.

## Not passed / not established

The opt-in `python3 tests/browser_fixture.py` check was attempted against a local synthetic page in real Chrome. Chrome returned **Apple Events JavaScript disabled**, so the job correctly ended as `needs_attention`. No successful browser-fixture transport is claimed. A tiny intended ChatGPT smoke job encountered the same prerequisite failure before any confirmed submission; it is not a successful real-model test.

An earlier interactive browser-control probe had succeeded, but the later window/context returned the disabled-permission error. The cause of that environment change was not established. Do not characterize it as a fixed code defect or ask the worker to bypass the browser setting.

Still required before claiming a working ChatGPT integration: stable browser-control permission, actual composer/send/message/completion selectors on the current website, a verified automatic send/wait/read journey, and appropriate provider permission. OpenAI's current [Terms of Use](https://openai.com/policies/terms-of-use/) restrict automated extraction of output; this project has not established an exception.

No host-specific wakeup, unattended machine-reboot recovery, Windows/Linux compatibility, savings percentage, independent quota pool, or unlimited/free model access has been demonstrated. Output correctness still requires the main agent's own acceptance checks.

## Reproduction

Follow the commands in the root README. The browser fixture creates only synthetic loopback input and does not call a model. A passing fixture would establish browser transport and selected recovery behavior, not permission or reliable execution on a real provider. Keep real job packets, account information and conversation output outside Git.

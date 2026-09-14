# Progress

## Current state
v1 step 1 done (repo, license, .gitignore, docs). v1 step 2 code done: src/input_monitor.py + tests/test_input_monitor.py written and passing (4/4), using a `.venv` with pynput + pytest. The code has NOT been run live yet — only tested against the DB logic directly, so no Accessibility permission has been requested and no real input has been captured.

## Next step
Decide with Marc whether to actually run `src/input_monitor.py` live (this triggers a real macOS Accessibility permission prompt and starts recording real key/click timestamps to agent_receipt.db). If yes: run it, verify a few real events land in the db, then stop it. After that: step 3, find where Claude Code/Desktop write their own logs (docs/SOURCES.md).

## Blockers
None currently — Marc's earlier concern (not wanting live logging while no agent is running) was addressed by keeping code-writing and test-running separate from actually starting the live listener. Still need his explicit go before running it live.

## Session log
<!-- Newest first. One entry per session: date, what got done, what's next. -->
### 2026-09-14 — Session 1
- Confirmed project name (Agent Receipt), OS (macOS).
- Fixed placeholder brackets in LICENSE.
- Initialized git repo, first commit with scaffolding (README, CLAUDE.md, LICENSE, .gitignore, docs/).
- Did NOT start the input monitor — Marc wants to decide separately when to turn that on.
- Next session: confirm which agents to watch for (Claude Code / Desktop / Chrome — Marc had no preference, so default to checking for all three when we get to step 3), then decide with Marc whether to build the input monitor.

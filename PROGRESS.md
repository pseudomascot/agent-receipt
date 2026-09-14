# Progress

## Current state
v1 step 2 done and verified live: src/input_monitor.py logs key/click timestamps to SQLite (id, timestamp, kind only). 4/4 automated tests pass. Live-tested on Marc's machine: 41 real events captured (37 key, 4 click) with no crash after fixing a thread-safety bug (pynput fires callbacks on its own threads; sqlite3 connection needed `check_same_thread=False` + a lock). Confirmed clean start/stop via SIGINT.

Permission note: the process tree for this Claude Code session runs through `/Applications/Claude.app` (not Terminal.app) — that's the app that needed Accessibility access, and it's what Marc granted. macOS Accessibility listed two separate entries (`Claude.app` and an embedded `claude.app` helper for claude-code); only the top-level `Claude.app` needed to be on.

## Next step
v1 step 3: find where Claude Code and Claude Desktop write their own logs, document in docs/SOURCES.md. No preference given yet on which agents to prioritize (Claude Code / Desktop / Chrome) — default to checking for all three.

## Blockers
None.

## Session log
<!-- Newest first. One entry per session: date, what got done, what's next. -->
### 2026-09-14 — Session 1
- Confirmed project name (Agent Receipt), OS (macOS).
- Fixed placeholder brackets in LICENSE.
- Initialized git repo, first commit with scaffolding (README, CLAUDE.md, LICENSE, .gitignore, docs/).
- Did NOT start the input monitor — Marc wants to decide separately when to turn that on.
- Next session: confirm which agents to watch for (Claude Code / Desktop / Chrome — Marc had no preference, so default to checking for all three when we get to step 3), then decide with Marc whether to build the input monitor.

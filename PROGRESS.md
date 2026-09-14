# Progress

## Current state
v1 steps 1-3 done. Step 2 (input monitor) verified live: 41 real events captured, thread-safety bug found and fixed. Step 3 (docs/SOURCES.md) done: Claude Code writes clean per-session JSONL transcripts to `~/.claude/projects/<project-path>/<session-uuid>.jsonl` with tool_use blocks — this is the source the log parser (step 4) will read. Claude Desktop's regular chat (non-Code tab) does NOT keep a usable local log — conversation/action data appears to sync to Anthropic's servers instead; only Electron operational logs are local. MCP tool calls appear inline in the Claude Code JSONL (no separate MCP log in use on this machine).

Permission note: the process tree for this Claude Code session runs through `/Applications/Claude.app` (not Terminal.app) — that's the app that needed Accessibility access for the input monitor, and it's what Marc granted.

## Next step
v1 step 4: the log parser — extract Write/Edit/NotebookEdit/Bash/mcp__* tool_use entries (with timestamps) from the Claude Code JSONL files into the `actions` table, per the extraction rules now written in docs/SOURCES.md.

## Blockers
None. Note for later: since Desktop-chat has no usable local log, v1's coverage is effectively "Claude Code sessions only" — the statement page should say that plainly rather than imply full coverage.

## Session log
<!-- Newest first. One entry per session: date, what got done, what's next. -->
### 2026-09-14 — Session 1
- Confirmed project name (Agent Receipt), OS (macOS).
- Fixed placeholder brackets in LICENSE.
- Initialized git repo, first commit with scaffolding (README, CLAUDE.md, LICENSE, .gitignore, docs/).
- Did NOT start the input monitor — Marc wants to decide separately when to turn that on.
- Next session: confirm which agents to watch for (Claude Code / Desktop / Chrome — Marc had no preference, so default to checking for all three when we get to step 3), then decide with Marc whether to build the input monitor.

# Progress

## Current state
v1 steps 1-7 done. Step 7 (daily summary, `src/summary.py`) writes a short plain-text statement per day to `summaries/YYYY-MM-DD.txt` (git-ignored) and the same text is shown live at the top of that day's page. Deterministic, from the database only — no AI, no network. Shared read queries moved to `src/queries.py`. Run with `./.venv/bin/python src/summary.py [YYYY-MM-DD]` (default: today).

Earlier: step 6 done. Step 6 (statement page, `src/statement.py` + `src/templates/`) is a Flask app bound to 127.0.0.1:8765. `/` lists days with agent/human/unknown counts; `/day/YYYY-MM-DD` shows totals by type and attribution, the input-monitor intervals for that day (with minutes), the actions table, a separate Unknown section, and a "what this receipt covers" table on every page (Claude Code: covered; physical input: partial; Desktop chat / email / card / wallet: not covered). Verified in the browser on real data. Run with `./.venv/bin/python src/statement.py`.

Earlier: step 5 done. Step 5 (correlator, `src/correlator.py`) applies the CLAUDE.md attribution rules to every action with a 30s window; safe to re-run. Added a `coverage` table (start/end per collector, heartbeated by the input monitor every 5s) so the receipt can distinguish "no physical input" from "monitor wasn't running" — the latter is what 1,713 of 1,716 current actions say, which is the truth: the monitor has only run for ~2 minutes so far. One coverage row was backfilled by hand for that test run (10:35:32–10:37:01 on 2026-09-14).

Earlier: step 4 done. Step 4 (log parser, `src/log_parser.py`) reads every Claude Code JSONL transcript and inserts side-effect tool calls into `actions` (schema now shared in `src/store.py`; `source_ref` UNIQUE column added so re-runs never double-count). Real run on Marc's machine: 9 transcripts → 1,718 actions (838 file_write, 676 execute/Bash, ~180 browser/MCP "other", 17 post). Known judgment calls to review during the week-long run: every Bash call is logged as `execute` even if the command was read-only (ls, git status); `browser_batch` is logged as one action even when it's only screenshots. Both err toward over-reporting, which is the safer direction.

Earlier steps: steps 1-3 done. Step 2 (input monitor) verified live: 41 real events captured, thread-safety bug found and fixed. Step 3 (docs/SOURCES.md) done: Claude Code writes clean per-session JSONL transcripts to `~/.claude/projects/<project-path>/<session-uuid>.jsonl` with tool_use blocks — this is the source the log parser (step 4) will read. Claude Desktop's regular chat (non-Code tab) does NOT keep a usable local log — conversation/action data appears to sync to Anthropic's servers instead; only Electron operational logs are local. MCP tool calls appear inline in the Claude Code JSONL (no separate MCP log in use on this machine).

Permission note: the process tree for this Claude Code session runs through `/Applications/Claude.app` (not Terminal.app) — that's the app that needed Accessibility access for the input monitor, and it's what Marc granted.

## Next step
v1 step 8: run it for a week, then review with Marc. Before the week can start, the pieces need to run without Marc babysitting a terminal: one command that starts the input monitor and the statement page, and re-runs parser + correlator + summary on a schedule (every few minutes is plenty). Decide the shape with Marc: a simple `run.py` loop launched from a `.command` file (least new machinery), a launchd agent (survives reboots), or the `rumps` menu-bar app from CLAUDE.md (nicest, one more dependency). Also: the input monitor only works when the launching app has Accessibility permission — a Terminal/.command launch will need Terminal added, unlike this session's Claude.app.

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

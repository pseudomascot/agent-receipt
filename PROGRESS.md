# Progress

## Current state
v1 steps 1-7 done; step 8 (week-long run) started 2026-09-14. Marc chose the double-click `.command` run mode. `src/run.py` starts the input monitor and statement page as child processes, opens the browser, and every 5 minutes runs parse → correlate → summary. `Agent Receipt.command` launches it; closing the Terminal window stops everything. SQLite now uses WAL + a 10s busy timeout since three processes share the file. Verified launched via Terminal: all three processes up, page serving, coverage heartbeat advancing.

Earlier: step 7 done. Step 7 (daily summary, `src/summary.py`) writes a short plain-text statement per day to `summaries/YYYY-MM-DD.txt` (git-ignored) and the same text is shown live at the top of that day's page. Deterministic, from the database only — no AI, no network. Shared read queries moved to `src/queries.py`. Run with `./.venv/bin/python src/summary.py [YYYY-MM-DD]` (default: today).

Earlier: step 6 done. Step 6 (statement page, `src/statement.py` + `src/templates/`) is a Flask app bound to 127.0.0.1:8765. `/` lists days with agent/human/unknown counts; `/day/YYYY-MM-DD` shows totals by type and attribution, the input-monitor intervals for that day (with minutes), the actions table, a separate Unknown section, and a "what this receipt covers" table on every page (Claude Code: covered; physical input: partial; Desktop chat / email / card / wallet: not covered). Verified in the browser on real data. Run with `./.venv/bin/python src/statement.py`.

Earlier: step 5 done. Step 5 (correlator, `src/correlator.py`) applies the CLAUDE.md attribution rules to every action with a 30s window; safe to re-run. Added a `coverage` table (start/end per collector, heartbeated by the input monitor every 5s) so the receipt can distinguish "no physical input" from "monitor wasn't running" — the latter is what 1,713 of 1,716 current actions say, which is the truth: the monitor has only run for ~2 minutes so far. One coverage row was backfilled by hand for that test run (10:35:32–10:37:01 on 2026-09-14).

Earlier: step 4 done. Step 4 (log parser, `src/log_parser.py`) reads every Claude Code JSONL transcript and inserts side-effect tool calls into `actions` (schema now shared in `src/store.py`; `source_ref` UNIQUE column added so re-runs never double-count). Real run on Marc's machine: 9 transcripts → 1,718 actions (838 file_write, 676 execute/Bash, ~180 browser/MCP "other", 17 post). Known judgment calls to review during the week-long run: every Bash call is logged as `execute` even if the command was read-only (ls, git status); `browser_batch` is logged as one action even when it's only screenshots. Both err toward over-reporting, which is the safer direction.

Earlier steps: steps 1-3 done. Step 2 (input monitor) verified live: 41 real events captured, thread-safety bug found and fixed. Step 3 (docs/SOURCES.md) done: Claude Code writes clean per-session JSONL transcripts to `~/.claude/projects/<project-path>/<session-uuid>.jsonl` with tool_use blocks — this is the source the log parser (step 4) will read. Claude Desktop's regular chat (non-Code tab) does NOT keep a usable local log — conversation/action data appears to sync to Anthropic's servers instead; only Electron operational logs are local. MCP tool calls appear inline in the Claude Code JSONL (no separate MCP log in use on this machine).

Permission note: the process tree for this Claude Code session runs through `/Applications/Claude.app` (not Terminal.app) — that's the app that needed Accessibility access for the input monitor, and it's what Marc granted.

## Next step
Let it run for a week (Marc double-clicks `Agent Receipt.command` whenever he's working; closing the window stops it). Review together around 2026-09-21: read the daily summaries, look for wrong or missing lines, decide on the Bash-always-counts and browser_batch judgment calls, and whether to keep the 30s window. Then v2 planning (email / card / wallet connectors, Twilio summary).

## Blockers
None. Note for later: since Desktop-chat has no usable local log, v1's coverage is effectively "Claude Code sessions only" — the statement page should say that plainly rather than imply full coverage.

## Session log
<!-- Newest first. One entry per session: date, what got done, what's next. -->
### 2026-09-14 — Session 1
- Confirmed project name (Agent Receipt), OS (macOS). Fixed LICENSE placeholder. Git repo initialized.
- Built and verified all of v1 in one session: input monitor (step 2), log sources documented (step 3), log parser (step 4), correlator + coverage tracking (step 5), statement page (step 6), daily summary (step 7), and the `.command` runner for the week-long trial (step 8 started). 23 tests.
- Marc's early concern — not wanting input logged while no agent is running — shaped two things: code was written and tested before anything ran live, and the monitor only runs while the `.command` window is open.
- Real-world checks: 41 events in the first live monitor test (found and fixed a thread-safety bug); ~1,700 actions parsed from 9 transcripts; the correlator correctly flagged 3 actions where Marc was typing within 30s of an agent action; today's summary says 69 of 72 actions happened while the monitor was off — which is true, and the point.
- Findings worth remembering: Accessibility permission belongs to the *launching app* (Claude.app for this session, Terminal for the `.command`); Claude Desktop chat has no usable local log, so v1 covers Claude Code only.
- Next: run for a week, review ~2026-09-21.

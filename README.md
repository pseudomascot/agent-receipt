# Agent Receipt

A local app that produces a daily statement of every side-effect action an AI
agent took on this machine. A bank statement for what your AI did. Local only;
nothing leaves the machine. MIT licensed.

Source: https://github.com/pseudomascot/agent-receipt

Why it's open source: it asks to watch your keyboard and mouse. You should be
able to read every line and confirm it records only *that* a key or click
happened, never which one — see `src/input_monitor.py` and the schema in
`src/store.py`.

## Running it
1. Double-click **`Agent Receipt.command`** in this folder. A Terminal window
   opens and stays open; the statement page opens in your browser at
   http://127.0.0.1:8765/.
2. The first time, macOS may say the input monitor is "not trusted". Open
   System Settings → Privacy & Security → Accessibility, turn on **Terminal**,
   then close the window and double-click the `.command` again.
3. Close the Terminal window to stop everything.

While it runs it re-reads the agent logs every 5 minutes, re-attributes every
action, and rewrites today's summary in `summaries/`.

**Needs review**: an irreversible action by a scheduled task (or with nobody
at the keyboard), a burst of irreversible actions, an unexplained action, or
a payment over a threshold raises a macOS notification within five minutes
and waits on the Needs review page until you mark it seen. All local.

On the page: one day, the last 7 or 30 days, or all time; filter by type,
agent, or user; **Download CSV** for the current view (opens in Excel or
Google Sheets; the footer carries a SHA-256 and the command to verify the rows
were not altered); **Print** for a paper statement or a PDF.

### What it records
- `input_events`: that a key or click happened, and when. Never which key,
  never where. Only while the `.command` window is open.
- `actions`: every side-effect tool call found in the agents' own transcripts
  — Claude Code, Claude Desktop's Cowork (including scheduled tasks), and
  OpenAI's Codex: file writes, shell commands, browser actions, posts.
  Any other agent joins by appending one JSON line per side effect to
  `~/.agent-receipt/inbox/` — see `docs/RECEIPT_LINE.md` and `examples/`.
  The model provider (OpenAI, Anthropic, Google, OpenRouter…) is irrelevant;
  what matters is the harness that acts, and whether it leaves a record.
- `coverage`: when the input monitor was actually running, so the statement
  can say "not looking" instead of "nothing happened".
- `transcript_chunks`: a SHA-256 of every byte range read from the agents'
  logs, re-checked every few hours, so the statement can say the logs were
  not altered after the receipt read them.

### What it does not see
Claude Desktop chat (outside the Code tab), email, cards, wallets. The page
says so on every screen.

## For developers
```
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/pytest
```
Pieces, each runnable on its own from `src/`: `input_monitor.py`,
`log_parser.py`, `correlator.py`, `summary.py [YYYY-MM-DD]`, `statement.py`.
`run.py` is what the `.command` starts.

## Project files
- `CLAUDE.md` — the project brief; Claude Code reads it every session.
- `PROGRESS.md` — where the project is; updated each session.
- `docs/DESIGN.md` — the reasoning behind the design.
- `docs/SOURCES.md` — where the agent logs live on this machine.

## Every later session with Claude Code
From this folder, run `claude` and paste:
```
Read CLAUDE.md and PROGRESS.md. Tell me where we are and what today's step is, then wait for me.
```

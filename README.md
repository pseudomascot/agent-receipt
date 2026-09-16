# Agent Receipt

A local app that produces a daily statement of every side-effect action an AI
agent took on this machine. A bank statement for what your AI did. Local only;
nothing leaves the machine. MIT licensed.

Source: https://github.com/pseudomascot/agent-receipt

Why it's open source: it asks to watch your keyboard and mouse. You should be
able to read every line and confirm it records only *that* a key or click
happened, never which one — see `src/input_monitor.py` and the schema in
`src/store.py`.

## Try it (macOS, five minutes)
1. **Get the folder.** On GitHub click **Code → Download ZIP**, unzip, and put
   the `agent-receipt` folder somewhere you'll keep it (Documents is fine).
   Or `git clone https://github.com/pseudomascot/agent-receipt`.
2. **Double-click `Setup.command`.** It checks for Python 3.11+, creates a
   private environment inside the folder, installs the two dependencies, and
   prints a report of what it found: which agents have logs on this Mac, and
   whether the input monitor has permission. Nothing runs yet.
   - If macOS refuses to open a `.command` file from the internet:
     right-click it → Open → Open.
   - If Python is missing, the report says so; install it from python.org.
3. **Grant the one permission.** System Settings → Privacy & Security →
   Accessibility → turn on **Terminal**. This lets the input monitor notice
   *that* you pressed a key or clicked — never which key, never where.
4. **Double-click `Agent Receipt.command`.** A Terminal window stays open and
   your statement opens at http://127.0.0.1:8765/. Close the window to stop.
5. Optional: open **Settings** in the app to connect a mailbox, Stripe, Ramp
   or Google Calendar. Nothing is required for the agents on this Mac.

The first run reads everything your agents have ever logged (tens of
thousands of lines take seconds), then refreshes every five minutes.

**Prefer no Terminal window?** Double-click **`Agent Receipt.app`** instead of
the `.command` (right-click → Open the first time). It runs as a small
menu-bar icon (🧾) with *Open statement*, *Needs review (N)*, *Status*, and
*Quit*, and the number next to the icon is how many receipt lines are waiting
for you. When macOS asks for the Accessibility permission it will name
"Agent Receipt" rather than Terminal. The app is a thin launcher that must
stay inside this folder; `make_app.command` rebuilds it.

## What you'll see
- **Front page:** days with activity, and a "This machine" table — which
  agents were found, which connectors are configured, whether the monitor is
  running.
- **A day:** every side-effect action — file written, command run, message
  sent, purchase — with who did it (`agent` / `human` / `unknown`), whether it
  can be undone, and why the receipt believes each of those.
- **Needs review:** an irreversible action by a scheduled task or with nobody
  at the keyboard, a burst of irreversible actions, an unexplained action, or
  a payment over a threshold; also a macOS notification within five minutes.
  The rules are adjustable on that page (threshold, whether people's own
  purchases count, burst size, each rule on/off); every change is recorded.
- **Download CSV** (opens in Excel or Google Sheets; the footer carries a
  checksum and the command to verify the rows were not altered) and **Print**
  (a paper statement or PDF).
- **Money:** only the rows with an amount, by month, with spent / received /
  net — and a three-column **QuickBooks / Xero** file your bookkeeper matches
  against the bank feed (`docs/BOOKKEEPING.md`). The receipt does not file
  taxes; it tells the bookkeeper who spent the money and why.
- **Agents:** every identity that has acted, in plain names ("Claude Code",
  "Sub-agent of Claude Code", "Cowork · scheduled task", "Google account")
  with a kind badge and the raw label underneath; give any of them your own
  name ("Bookkeeping bot") and it is used everywhere. Each has first/last
  seen, counts, open alerts, and a **Retire** switch. Retiring never removes
  history; it records the decision on the receipt and flags anything the
  agent does afterwards (`docs/AGENTS.md`).
- **Stop:** cut an agent off. Real API calls where the issuer has one
  (freeze/cancel Stripe Issuing cards, revoke the receipt's own Google
  token), ending running Codex / Claude Code processes, and open-plus-steps
  where there is no API (Gmail, Stripe keys, Cowork schedules). Each button
  says whether it *stops the agent* or only *blinds the receipt*; every press
  is recorded (`docs/STOP.md`).
- **Coverage**, on every page: what is and isn't being watched. A receipt with
  silent gaps is worse than none.

## What it records
- `input_events`: that a key or click happened, and when. Never which key,
  never where. Only while the `.command` window is open.
- `actions`: every side-effect tool call found in the agents' own transcripts
  — Claude Code, Claude Desktop's Cowork (including scheduled tasks), OpenAI's
  Codex, and Cursor's agent (whichever model it runs — Grok, Claude, GPT):
  file writes, shell commands, browser actions, posts.
  Any other agent joins by appending one JSON line per side effect to
  `~/.agent-receipt/inbox/` — see `docs/RECEIPT_LINE.md` and `examples/`.
  The model provider (OpenAI, Anthropic, Google, OpenRouter…) is irrelevant;
  what matters is the harness that acts, and whether it leaves a record.
- `coverage`: when the input monitor was actually running, so the statement
  can say "not looking" instead of "nothing happened".
- `transcript_chunks`: a SHA-256 of every byte range read from the agents'
  logs, re-checked every few hours, so the statement can say the logs were
  not altered after the receipt read them.

## Optional connectors
All of these are set up from the app's **Settings** page
(`http://127.0.0.1:8765/settings`, `docs/SETTINGS.md`) — each has a Test
button — or by hand in the private `.env`.
- **Email and card alerts:** give the agent its own mailbox and enter its IMAP
  login (`docs/EMAIL.md`). Sent mail becomes
  `send_email` rows; card-issuer alert emails become `purchase` rows.
- **Stripe:** a key in `.env` (`docs/STRIPE.md`). Charges the agent collects,
  and purchases on its own Issuing card where Issuing is activated.
- **Ramp (a card and budget per agent):** client credentials in `.env`
  (`docs/RAMP.md`). The Agents page can *give an agent its own Ramp fund* —
  a budget that issues a virtual card — so its purchases are its own by
  construction and the Stop page can suspend or terminate just that card.
  Sandbox by default; declines are recorded too.

## What it does not see
Claude Desktop chat (outside the Code tab), Google Antigravity, and — until
configured — email, cards, and wallets. **Agents that run in the cloud**
(Claude on the web, Cursor or Codex cloud agents, hosted agents) leave no log
on the Mac: they are seen only through the mailbox, card or account you have
given them — their consequences, not their commands. The page says so on
every screen.

## For developers
```
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/pytest
```
Pieces, each runnable on its own from `src/`: `input_monitor.py`,
`log_parser.py`, `correlator.py`, `summary.py [YYYY-MM-DD]`, `statement.py`,
`verify.py`, `doctor.py`, `email_connector.py`, `stripe_connector.py`.
`run.py` is what the `.command` starts.

## Project files
- `CLAUDE.md` — the project brief; Claude Code reads it every session.
- `PROGRESS.md` — where the project is; updated each session.
- `docs/DESIGN.md` — the reasoning behind the design.
- `docs/SOURCES.md` — where each agent's logs live and how they are read.
- `docs/RECEIPT_LINE.md` — the open format any agent can write.

## Support
Agent Receipt is free and MIT-licensed, built by one person. If it's useful
to you, a coffee is appreciated: https://ko-fi.com/pseudomascot. If you're
using it for a business and want something sooner — a Windows version, an
insurer-ready export — open an issue and say so; that's worth more than a tip.

## Every later session with Claude Code
From this folder, run `claude` and paste:
```
Read CLAUDE.md and PROGRESS.md. Tell me where we are and what today's step is, then wait for me.
```

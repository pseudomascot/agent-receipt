# Agent Receipt (working name)

## What this is
A local app that produces a daily statement of every action an AI agent took on this machine, and later on accounts it can access. A bank statement for what your AI did.

Side effects only. Each line: time, which agent, what it did, to whom or what, amount if money, link to the artifact, reversible or not, and how we know it was the agent.

## Who you're working with
Marc. He does not code. Treat every session as pair programming with a smart non-programmer:
- One step at a time. Wait for confirmation before the next step.
- Before running any command, say in one sentence what it does.
- Never assume a tool is installed. Check.
- Ask before installing anything or adding a dependency.
- When something fails, explain what happened in plain English before fixing it.
- Update PROGRESS.md at the end of every session.

## Non-negotiable rules
1. Timestamps only. The input monitor records that a key or click happened and when. Never which key. Never mouse position. Never screenshots. Never window titles unless Marc approves a specific app.
2. Never guess attribution. Every action is `agent`, `human`, or `unknown`. Unknown is a valid answer and gets its own section on the statement.
3. Side effects only. Reads are not logged.
4. Local only in v1. Nothing leaves the machine. No cloud, no telemetry.
5. Open source from day one. MIT license. Code must be readable by a suspicious stranger.
6. No secrets in the repo. No card numbers ever, anywhere.

## Architecture (three parts)
1. **Collector** — background process. v1 sources: (a) physical input monitor, (b) agent logs already on this machine (Claude Code, Claude Desktop, MCP servers). v2 adds: agent mailbox (read-only), card transaction alerts, wallet address.
2. **Store** — one SQLite database. Main table `actions`.
3. **Statement** — a local web page (localhost) listing actions by day with totals, plus a daily summary. v2 sends the summary by text or email.

## How attribution works
- Agent log shows a side-effect tool call at time T → `agent` (source: log).
- Physical input in the N seconds before an observed action, and no matching agent log entry → `human`.
- Neither → `unknown`.
- Both (agent log entry AND recent physical input) → `agent`, with a note. The log wins because it is a declaration, not an inference.

N is configurable. Start at 30 seconds.

## Data model (start here, change with a reason)
`actions`: id, timestamp, agent, source (log | input | email | card | wallet), action_type (send_email | create_event | purchase | file_write | post | execute | other), target, amount, currency, artifact_link, reversible, attribution (agent | human | unknown), confidence_note, raw_json, source_ref

`source_ref` (added session 1): the source's own id for the action (e.g. the tool-call id in a Claude Code transcript), UNIQUE, so re-parsing a log never double-counts.

`input_events`: id, timestamp, kind (key | click). Nothing else. Ever.

## Build order

### v1 (this milestone)
1. Confirm OS. Set up repo, license, .gitignore, PROGRESS.md.
2. Input monitor: logs key/click timestamps to SQLite. Verify with a test.
3. Find where Claude Code and Claude Desktop write logs on this machine. Do not assume paths; locate them. Document in docs/SOURCES.md.
4. Log parser: extract side-effect tool calls (writes, sends, executes) with timestamps into `actions`.
5. Correlator: apply the attribution rules.
6. Statement page: localhost, grouped by day, totals, unknown section.
7. Daily summary: written to a file and shown on the page. (Text delivery is v2.)
8. Run it for a week. Review with Marc. Fix what's wrong.

### v2
Email connector (agent's own mailbox, read-only scope). Card alert parser. Wallet poller. Twilio summary. Alert thresholds ("text me if any charge is over $X").

### v3
Other agents' logs. Hosted statement page. Business version.

## Tech (defaults; propose a change only with reasons)
Python 3.11+. `pynput` for input events. SQLite. FastAPI or Flask for the local page. Git.
macOS: menu bar via `rumps`. Windows: tray via `pystray`. Decide after OS is confirmed.

## Decisions already made (don't relitigate without new information)
- Not a browser extension. An extension sees only the browser.
- Not built to sell to Anthropic or OpenAI. Independence across agents is the product.
- No AI-vs-human inference from writing style or timing. Ever.
- Agents should ideally have their own mailbox, card, and wallet, but v1 does not require it.
- Undo (a hold window before actions execute) is a later feature that reuses this plumbing. Not v1.

## Open questions (ask Marc in session 1)
- Mac or Windows?
- Project name?
- Which agents run on this machine? (Claude Code, Claude Desktop, Claude in Chrome, others)
- Any app he wants excluded from input monitoring?

## Session start ritual
1. Read PROGRESS.md.
2. State in two sentences where the project is and what today's step is.
3. Ask Marc if that's the plan. Then go.

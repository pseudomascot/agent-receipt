# Agents: identities, retiring, and what "stopping" really means

## Where an identity comes from
The receipt does not hand out identities; it observes them. An agent's label
is one of:

- **Its own log** — Claude Code, Cowork (including scheduled tasks), Codex.
  The harness wrote the transcript; the label is the harness (and, where the
  log says so, the session or workspace).
- **A credential the receipt reads** — the agent's mailbox, its Stripe
  account, its Google account. Anything done under that login is the agent's.
- **A declaration** — any agent that appends receipt lines to
  `~/.agent-receipt/inbox/` names itself (`docs/RECEIPT_LINE.md`).

**Sub-agents are told apart.** When Claude Code spawns a worker, the worker
writes its own transcript (`<session>/subagents/agent-<id>.jsonl`, verified
on this Mac — see `docs/SOURCES.md`). The receipt reads it and labels the
worker `claude-code (<entrypoint>) / <role>: <task>` — for example
`claude-code (claude-desktop) / Explore: Read-only test sub-agent` — so each
worker is its own row on the Agents page and can be filtered, reviewed and
retired on its own. The spawn itself appears on the parent's statement as
"Started a sub-agent: …", so a worker's rows always have a parent. Other
frameworks name their workers through the inbox.

## The Agents page
`http://127.0.0.1:8765/agents` lists every identity: what it is, which users
it ran under, first and last seen, how many actions (and how many can't be
undone or involved money), open Needs-review items, and its status.
Configured connectors that have not produced anything yet are listed too,
so you can see an agent exists before it acts.

## Retire
**Retire** records that, from this moment, the agent is not supposed to act.

- Nothing it did is deleted or hidden. Its rows stay on every statement, in
  every export, under the integrity check. A receipt that forgets is not a
  receipt.
- The press itself becomes a line on the receipt — "Retired agent: cowork
  (scheduled task) — kept deleting things" — attributed to the person who
  pressed it, with the optional reason.
- Anything the agent does afterwards raises a **retired agent acted** alert
  (a macOS notification within five minutes, and an entry on Needs review).
  That is the evidence a reviewer wants: it was told to stop, and did not.
- **Restore** undoes it, and is recorded the same way.

On statements, a retired agent's chip reads "name · retired".

## What retiring does not do
It does not stop the agent. The receipt watches; it does not control. To
actually stop an agent you revoke what it acts with — its mailbox password,
its card, its Google login, its API key — at the issuer. Where the issuer has
an API (a Stripe Issuing card, a Google Workspace user) that can be a button
in this app; where it does not (a consumer Gmail account, a pasted Stripe
key) it is a link and a set of steps. That kill-switch panel is the next
piece of work; see PROGRESS.md.

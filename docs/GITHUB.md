# GitHub: what an agent's own account committed and opened

For agents that run **in the cloud** — Claude Code on the web, Cursor cloud
agents, Codex in the cloud, a company's hosted agents — there is no log on
this Mac. But coding agents finish their work the same way: commits and pull
requests. Give the agent **its own GitHub account** (the same principle as
its own mailbox and card), list it on the Settings page, and every commit and
pull request that account makes is on the receipt, attributed to it by
credential.

## What you need
- **A token with read access** to the repositories: on GitHub, Settings →
  Developer settings → Personal access tokens → *Fine-grained* → repository
  access to the repos you want watched, permissions **Contents: read**,
  **Metadata: read**, **Pull requests: read**. Yours or the agent's; the
  receipt only reads.
- **The agents' GitHub usernames** (`RECEIPT_GITHUB_AGENT_LOGINS`, comma-
  separated). Only their commits and pull requests are read: a person's own
  commits are their business, and a local agent's commits are already on the
  receipt from its transcript — reading everyone would double-count.
- Optionally **which repositories** (`RECEIPT_GITHUB_REPOS`, `owner/repo`);
  empty means every repo the token can see (owner, collaborator, org member).

Enter them on `http://127.0.0.1:8765/settings` → GitHub card → **Test**
("token works (signed in as …); 3 repos to watch; agent accounts: acme-bot").

## What appears on the receipt
- One **File edits** row per commit: `acme/site@abc1234: Fix checkout bug — 8
  files: src/a.py, … +2 more`, linked to the commit on GitHub, at the commit's
  own author time, attributed `agent` = *GitHub account acme-bot*, **not
  undoable** ("published to GitHub — a new commit can undo the change, but it
  has been visible to others").
- One **Messages & posts** row per pull request opened: `acme/site#7: Add
  retries (open)`, linked, undoable (a PR can be closed).
- Polled every refresh; incremental with a two-day overlap; idempotent by
  commit SHA / PR number.

## Honest limits
- **Consequences, not commands.** The receipt sees what landed on GitHub, not
  the shell commands or the files edited and then discarded. For a local
  agent the transcript gives you that; for a cloud agent nothing does unless
  the vendor exposes its session log (see PROGRESS.md for that route).
- **Only listed accounts.** An agent committing under a person's identity
  (a shared token) is invisible here — which is the point of giving it its
  own.
- **Commits, not pushes.** A commit's author time is when it was written,
  which can be earlier than when it reached GitHub.
- The Stop page's control for a GitHub account is a link with steps (remove
  from repos / organisation, revoke its tokens): GitHub has no API to suspend
  someone else's account.

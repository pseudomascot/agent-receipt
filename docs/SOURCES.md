# Sources on this machine

Filled in during session 1 (2026-09-14), on Marc's Mac.

## Claude Code
- Log location: `~/.claude/projects/<sanitized-project-path>/<session-uuid>.jsonl`
  — one file per session. `<sanitized-project-path>` is the working directory's
  absolute path with `/` replaced by `-` (e.g. a project at
  `/Users/marc/Desktop/Foo` lands in `~/.claude/projects/-Users-marc-Desktop-Foo/`).
- Format: JSON Lines — one JSON object per line, in chronological order.
  Relevant fields seen on this machine:
  - `type`: `user` | `assistant` | `system` | others (queue/title bookkeeping)
  - `timestamp`: ISO 8601, present on every line
  - `sessionId`, `cwd`, `gitBranch`: session context
  - `message.content[]`: array of blocks; a block with `"type": "tool_use"`
    carries `name` (the tool) and `input` (its arguments)
  - a later line carries the matching result via `toolUseID` / `toolUseResult`
- Side-effect entries to extract (v1): tool_use blocks where `name` is:
  - `Write`, `Edit`, `NotebookEdit` → file writes
  - `Bash` → treat as a side effect conservatively (a shell command can do
    anything); action_type `execute`, target = the command
  - anything starting with `mcp__` that acts in the world (e.g. browser
    automation, sending a file, publishing an artifact) — the `mcp__<server>__<tool>`
    name identifies both the source and the action
  - Excluded (reads, not side effects): `Read`, `Glob`, `Grep`, `TodoWrite`
    (local-only), `AskUserQuestion`, `ExitPlanMode`
- Note: this machine runs Claude Code both standalone and embedded inside the
  Claude desktop app's "Code" tab — both write to the exact same location in
  the exact same format. Confirmed by tracing this very session's process
  tree back to `/Applications/Claude.app`, and finding this session's own
  transcript file in the expected place.

## Cowork (Claude Desktop's "local agent mode") — added 2026-09-14
- Log location: `~/Library/Application Support/Claude/local-agent-mode-sessions/<org>/<account>/local_<session>/audit.jsonl`,
  with a sidecar `local_<session>.json` next to each session folder carrying
  `title`, `cwd`, `sessionType` (`scheduled` for scheduled tasks), `scheduledTaskId`,
  `createdAt`, `model`, `accountName`. 128 sessions / 5.9 GB on this machine as of
  2026-09-14 (one file is 1.9 GB), 98 of them scheduled tasks.
- Format: JSON Lines, the `claude` CLI's `--output-format stream-json` wrapped
  by the desktop app: every line gets `_audit_timestamp` (ISO) and `_audit_hmac`.
  Assistant lines carry `session_id`, `uuid`, and `message.content[]` with
  `tool_use` blocks exactly like Claude Code. `cwd` is a path inside the
  sandbox VM (`/sessions/<name>`), so the sidecar title is used as the project
  label instead.
- Side-effect entries to extract: same rules as Claude Code, plus
  `mcp__workspace__bash` (the sandbox's shell) treated like `Bash`. Browser
  actions arrive as `mcp__Claude_in_Chrome__*` — Claude in Chrome driven from
  Cowork — and are classified by the same browser rules.
- Agent label: `cowork` or `cowork (scheduled task)`. Scheduled tasks run with
  nobody at the keyboard, which is exactly the case the receipt exists for.
- `_audit_hmac` + `.audit-key` (a 51-byte binary key per session, next to
  `audit.jsonl`): tried to reproduce the signature on 2026-09-14 — HMAC
  SHA-256/512/SHA3/BLAKE2 over the line with the hmac field removed (compact,
  sorted, ASCII/UTF-8 variants, with/without the audit fields, timestamp
  prefixed/suffixed, previous-hmac chaining), keyed by the raw bytes, the
  stripped bytes, base64/hex decodings, and SHA-256 of the key. Nothing
  matched. The scheme needs something only the desktop app has. Not pursued
  further; the receipt keeps its own integrity record instead (see below).

## Log integrity (the receipt's own tamper-evidence)
- While ingesting, every byte range the parser consumes is hashed
  (`transcript_chunks`: path, start, end, sha256, first_seen) — no extra I/O.
- `src/verify.py` re-reads those ranges and compares; the runner does this at
  start and every 6 hours; the result is shown on every page and in the summary.
- Claim it supports: "not altered since the receipt first read it on <date>."
  Claim it does not support: that the log was honest at that moment.
- Performance: transcripts are streamed line by line and only lines containing
  `"tool_use"` are JSON-parsed; the first pass over 5.9 GB takes minutes,
  afterwards only appended bytes are read.

## Claude Desktop (regular chat, not the Code tab)
- Checked: `~/Library/Logs/Claude/*.log` (main.log, claude.ai-web.log,
  cowork_vm_node.log, coworkd.log, etc.)
- Format: plain-text timestamped lines: `YYYY-MM-DD HH:MM:SS [level] [component] message`
- Finding: these are Electron app operational/diagnostic logs — process
  memory, websocket/bridge connections (`sessions-bridge`, `bridge-ws`,
  `oauth-v2`), skill sync status. They do **not** contain conversation
  content or a structured record of tool calls/side effects. (Checked
  specifically for "tool_use" mentions — the only hits were an environment
  variable name, `CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES`, not actual data.)
- Conclusion: regular Desktop chat appears to sync conversation/action data
  to Anthropic's servers rather than keeping a local structured log. v1's
  log parser cannot extract Desktop-chat side effects this way. If this
  matters later, the more promising angle is watching what Desktop actually
  does on disk (downloads, saved artifacts) rather than parsing this log.

## MCP servers
- Which ones are installed: varies by session (seen in this session:
  Claude_Browser, ccd_session, visualize, and others) — no single
  machine-wide config file inventory found yet; revisit if this becomes
  important.
- Where they log: `~/Library/Logs/Claude/mcp.log` exists but is 0 bytes on
  this machine — not in use. MCP tool calls instead appear inline in the
  same Claude Code JSONL transcripts, as `tool_use` blocks named
  `mcp__<server>__<tool>`. No separate MCP source needed for v1's parser.

## Other agents
- None identified yet. Ask Marc if any other AI CLIs/agents run on this
  machine beyond Claude Code and Claude Desktop.

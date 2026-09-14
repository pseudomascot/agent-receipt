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

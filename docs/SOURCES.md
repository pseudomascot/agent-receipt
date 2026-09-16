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
- **Sub-agents (verified 2026-09-15 by spawning one read-only test agent):**
  a sub-agent's work is NOT in the parent's file. It gets its own transcript at
  `<project>/<session-uuid>/subagents/agent-<agentId>.jsonl`, same line format,
  every line flagged `"isSidechain": true` with `agentId`, `sessionId` = the
  parent session, `cwd`, and `attributionAgent` (the agent type) on assistant
  lines. A sidecar `agent-<agentId>.meta.json` holds `agentType`,
  `description` (the parent's one-line label for the task), `toolUseId` (the
  parent's `Agent` tool_use id), `spawnDepth`. The parent's transcript has the
  `Agent` tool_use (input: `description`, `prompt`, `subagent_type`) and a
  result carrying `agentId`. The receipt therefore labels a worker
  `claude-code (<entrypoint>) / <agentType>: <description>` and records the
  spawn itself as "Started a sub-agent". Before today no sub-agent had ever
  run on this Mac (all 10,999 lines had `isSidechain: false`).
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

## Codex (OpenAI) — added 2026-09-14
- Log location: `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<id>.jsonl`
  (8 sessions / 346 MB on this machine; the app is `com.openai.codex`, no CLI
  on PATH). `~/.codex/thread_history_1.sqlite` holds the same items in a
  `thread_items` table; the rollout files are used because they are
  append-only and fit the streaming/hashing reader.
- Format: JSON Lines with `timestamp` (ISO), `type`, `payload`, `ordinal`.
  Line 1 is `session_meta` (`payload.cwd`, `payload.id`, `payload.originator`
  such as `codex_work_desktop`, `payload.source`). Actions are `event_msg`
  lines whose `payload.type` is `item_completed`; `payload.item.type` is one of
  `CommandExecution` (command, cwd, exit_code), `FileChange` (changes[] with
  path and kind add/update/delete), `McpToolCall` (server, tool, arguments),
  `Extension` (e.g. image generation with savedPath), plus non-actions
  (Reasoning, AgentMessage, SubAgentActivity, CollabAgentToolCall…).
- Side-effect entries: CommandExecution → `execute` (same read-only filter as
  Bash); FileChange add/update → `file_write`, delete → `file_write` marked
  irreversible; McpToolCall → the same MCP rules; Extension with a savedPath →
  `file_write`. `response_item` function calls (`send_message`, `spawn_agent`,
  `wait_agent`…) are Codex's internal multi-agent chatter and are ignored.
- Agent label: `codex (<originator>)`.
- Quirks seen in real data: `command` is an argv list, almost always
  `["/bin/zsh", "-lc", "<the real command>"]` — the parser unwraps that so the
  read-only filter and the page see the real command; `changes[]` entries are
  sometimes bare path strings instead of objects (treated as an edit of that
  path).

## Cursor — added 2026-09-16
- Verified by installing Cursor (its agent-first window, model "Cursor Grok 4.6")
  and running one task in a throwaway folder: create `hello.txt`, run `ls`.
- Log location: `~/.cursor/projects/<sanitized-folder>/agent-transcripts/<conversation-id>/<conversation-id>.jsonl`
  — one JSONL file per conversation. `<sanitized-folder>` is the working
  directory with `/` replaced by `-` (`Users-marc-Desktop-cursor-test`); the
  parser rebuilds the real path by checking the disk for the longest existing
  prefix, so hyphenated folder names survive. `empty-window` means no folder.
- Line shapes: `{"role":"user","message":{"content":[{"type":"text","text":"<timestamp>Wednesday, Sep 16, 2026, 8:21 AM (UTC-4)</timestamp>\n<user_query>…</user_query>"}]}}`,
  `{"role":"assistant","message":{"content":[{"type":"text",…},{"type":"tool_use","name":"Write","input":{"path":…,"contents":…}},{"type":"tool_use","name":"Shell","input":{"command":"ls","description":…}}]}}`,
  `{"type":"turn_ended","status":"success"}`. **No per-line timestamp, no tool-call ids, no cwd**: the
  parser dates every tool call in a turn from the user line's `<timestamp>` tag
  (minute precision — said so in each row's note) and falls back to the file's
  modification time; the call id is the file position.
- Tool mapping: `Write`/`Edit`/`StrReplace`/… → file_write (path made absolute
  against the folder); `Shell`/`Bash`/`RunTerminalCommand` → execute (same
  read-only filter as Claude Code, so `ls` is not a row); `Delete` → file
  deletion; `Read`/`Grep`/`Glob`/`Search`/… skipped; any other tool → `other`
  as `cursor:<name>` so nothing is silently dropped.
- Model and title come from Cursor's state database, read read-only:
  `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`, table
  `cursorDiskKV`, key `composerData:<conversation-id>` → `modelConfig.modelName`,
  `name`. The agent label is `cursor (<model>)` — so **Grok, Claude or GPT inside
  Cursor** all appear as Cursor with the model named; the receipt tracks the
  harness, not the model. If the db is unreadable the label is plain `cursor`.
- Richer but unused: the same db holds one `bubbleId:<conv>:<id>` row per step
  with exact `createdAt` and `toolFormerData` (`edit_file_v2`,
  `run_terminal_command_v2`, params, status); and
  `~/.cursor/ai-tracking/ai-code-tracking.db` records every AI-written file with
  a millisecond timestamp, model and conversation id. Either could refine the
  minute-precision times later.
- Cursor's classic IDE mode keeps chats in `workspaceStorage/<hash>/state.vscdb`
  instead; not covered until seen.

## Inbox — receipt lines any agent writes itself (added 2026-09-14)
- `~/.agent-receipt/inbox/*.jsonl`, format in docs/RECEIPT_LINE.md, helpers in
  `examples/`. The agent declares the action type, target, amount, id,
  reversibility. Note reads "declared by the agent itself via the receipt inbox".

## Google Antigravity — not covered
- `~/.gemini/antigravity/conversations/*.pb` (14 files, 83 MB) and `brain/`
  (md/json/png). Conversations are protobuf with no published schema; not
  readable without it. Revisit if Google documents the format.

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

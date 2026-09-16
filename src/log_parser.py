"""Log parser: side-effect actions from agent transcripts -> `actions`.

Sources (documented in docs/SOURCES.md):
- Claude Code: ~/.claude/projects/**/*.jsonl
- Cowork (Claude Desktop's local agent mode): local-agent-mode-sessions/**/audit.jsonl
- Codex (OpenAI): ~/.codex/sessions/**/*.jsonl
- Inbox: ~/.agent-receipt/inbox/*.jsonl — lines any agent writes itself (docs/RECEIPT_LINE.md)

Each source says how to turn one transcript line into zero or more `Call`s (a
tool name plus its input, or a declared action). Reads are skipped on purpose.
Incremental: a byte offset per transcript is kept in `parser_state`, so a
refresh only parses what was appended. Re-running is always safe: each call's
own id is stored as `source_ref` (UNIQUE).
"""

import getpass
import hashlib
import json
import re
import shlex
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from reversibility import assess
from shell_classify import is_read_only
from store import DB_PATH, connect

# Bumping this deletes every log-sourced row and re-extracts all transcripts.
# Do it whenever what we extract, or how we judge it, changes.
RULES_VERSION = "11"

ACTION_TYPES = {"send_email", "create_event", "purchase", "file_write", "post", "execute", "other"}
FILE_WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
EXECUTE_TOOLS = {"Bash"}
POST_TOOLS = {"SendUserFile"}
# Artifact is a mixed tool: these `action` values change something; the rest read.
ARTIFACT_WRITE_ACTIONS = {None, "publish", "write_db", "upload_asset", "delete_asset",
                          "reply", "resolve", "delete", "pin", "unpin"}
# MCP tools are named mcp__<server>__<tool>. A tool whose name starts with one
# of these is a read and is skipped. Everything else under mcp__ is logged.
MCP_READ_PREFIXES = ("read", "get_", "list", "find", "search", "screenshot",
                     "tabs_context", "status", "preview_list", "preview_logs", "web_fetch",
                     "wait", "zoom", "scroll", "app_screenshot", "app_ax_find", "app_list")
# MCP tools (by their name after the server prefix) that only change the chat
# or browser UI, ask the user for permission, or otherwise don't touch the world.
UI_ONLY_MCP_TOOLS = {"mark_chapter", "show_widget", "read_me", "tabs_select", "tabs_create",
                     "tabs_close", "tabs_create_mcp", "tabs_close_mcp", "resize_window",
                     "switch_browser", "select_browser", "present_files", "request_access",
                     "request_cowork_directory", "request_full_control", "release_full_control",
                     "request_teach_access", "app_release"}
# MCP tools that run a shell command (Cowork's sandbox exposes one).
MCP_SHELL_TOOLS = {"bash"}
# MCP tools whose name says what kind of side effect they are. Checked after the
# read/UI filters, by regular expression on the part after the server prefix.
MCP_TYPE_PATTERNS = (
    ("send_email", r"^(send_(e?mail|gmail)|gmail_send|mail_send|send_message_gmail)"),
    ("create_event", r"^(create_(calendar_)?event|calendar_create|add_event|schedule_meeting)"),
    ("purchase", r"^(purchase|place_order|checkout|create_(payment|charge|order)|pay$|buy)"),
    ("post", r"^(send_message|post_message|send_sms|send_text|reply|post$|publish|tweet)"),
)
# Browser automation: `computer` actions that only look, and batch items that only look.
BROWSER_READ_ACTIONS = {"screenshot", "zoom", "wait", "scroll", "scroll_to", "hover"}
BROWSER_READ_TOOLS = {"read_page", "find", "get_page_text", "read_console_messages",
                      "read_network_requests", "tabs_context", "tabs_context_mcp",
                      "tabs_select", "resize_window"}

TARGET_MAX_CHARS = 500
RAW_STRING_MAX_CHARS = 10_000


# --- calls and sources -------------------------------------------------------

@dataclass
class Call:
    """One thing an agent did, as a tool name + input (the shape classify() reads)."""
    name: str
    input: dict
    call_id: str | None = None
    session_id: str | None = None
    cwd: str | None = None
    # Optional facts the source already knows, which win over classify()/assess():
    # action_type, target, artifact_link, amount, currency, reversible, reason, agent.
    overrides: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Source:
    name: str
    root: Path
    pattern: str
    extract: Callable[[dict, dict], list]        # (entry, session_meta) -> [Call]
    agent_name: Callable[[dict, dict], str]      # (entry, session_meta) -> label
    timestamp: Callable[[dict], str | None]      # entry -> ISO string
    session_meta: Callable[[Path], dict]         # transcript path -> metadata dict
    line_hint: str | None = None                 # substring a useful line must contain


def _tool_use_calls(entry: dict, meta: dict) -> list:
    """Claude Code and Cowork: assistant messages with tool_use content blocks."""
    if entry.get("type") != "assistant":
        return []
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    session_id = entry.get("sessionId") or entry.get("session_id")
    cwd = entry.get("cwd") or meta.get("cwd")
    calls = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
            calls.append(Call(block.get("name") or "", tool_input, block.get("id"), session_id, cwd))
    return calls


def _claude_code_agent(entry: dict, meta: dict) -> str:
    """`claude-code (<entrypoint>)`, plus ` / <role>: <task>` for a sub-agent's own transcript."""
    entrypoint = entry.get("entrypoint")
    name = f"claude-code ({entrypoint})" if entrypoint else "claude-code"
    if entry.get("isSidechain") or entry.get("agentId") or meta.get("agentType"):
        role = meta.get("agentType") or entry.get("attributionAgent") or "subagent"
        task = " ".join(str(meta.get("description") or "").split())
        if task:
            name += f" / {role}: {task[:48]}"
        else:
            name += f" / {role} {str(entry.get('agentId') or '')[:8]}".rstrip()
    return name


def _claude_code_meta(path: Path) -> dict:
    """A sub-agent transcript (<session>/subagents/agent-<id>.jsonl) has a sidecar
    agent-<id>.meta.json with agentType, description, toolUseId, spawnDepth."""
    if path.parent.name != "subagents":
        return {}
    meta = {"parent_session": path.parent.parent.name}
    sidecar = path.with_name(path.stem + ".meta.json")
    try:
        with open(sidecar, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            meta.update({k: data[k] for k in ("agentType", "description", "toolUseId", "spawnDepth") if k in data})
    except (OSError, ValueError):
        pass
    return meta


def _cowork_agent(_entry: dict, meta: dict) -> str:
    return "cowork (scheduled task)" if meta.get("sessionType") == "scheduled" else "cowork"


def _cowork_meta(path: Path) -> dict:
    """Cowork keeps <session>/audit.jsonl next to <session>.json with title, cwd, type."""
    sidecar = path.parent.with_suffix(".json")
    try:
        with open(sidecar, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {
        "title": data.get("title"),
        "cwd": data.get("cwd"),
        "sessionType": data.get("sessionType"),
        "scheduledTaskId": data.get("scheduledTaskId"),
    }


def _codex_meta(path: Path) -> dict:
    """Codex rollouts start with a session_meta line: cwd, id, originator, source."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = json.loads(f.readline())
    except (OSError, ValueError):
        return {}
    if first.get("type") != "session_meta":
        return {}
    payload = first.get("payload") or {}
    return {"cwd": payload.get("cwd"), "session_id": payload.get("id"),
            "originator": payload.get("originator"), "source": payload.get("source")}


def _codex_calls(entry: dict, meta: dict) -> list:
    """Codex: event_msg lines whose payload is a completed typed item."""
    payload = entry.get("payload") or {}
    if entry.get("type") != "event_msg" or payload.get("type") != "item_completed":
        return []
    item = payload.get("item") or {}
    kind = item.get("type")
    item_id = item.get("id")
    sid = meta.get("session_id")
    cwd = item.get("cwd") or meta.get("cwd")
    if isinstance(cwd, str) and cwd.startswith("file://"):
        cwd = cwd[len("file://"):]
    if kind == "CommandExecution":
        return [Call("Bash", {"command": _command_text(item.get("command")), "cwd": cwd,
                              "exit_code": item.get("exit_code")}, item_id, sid, cwd)]
    if kind == "FileChange":
        calls = []
        for i, change in enumerate(item.get("changes") or []):
            if isinstance(change, str):
                change = {"path": change}
            if not isinstance(change, dict):
                continue
            path = change.get("path")
            kind = change.get("kind")
            kind_type = ((kind.get("type") if isinstance(kind, dict) else kind) or "").lower()
            if kind_type == "delete":
                calls.append(Call("codex:file_delete", {"file_path": path}, f"{item_id}:{i}", sid, cwd,
                                  {"action_type": "file_write", "target": path,
                                   "reversible": 0, "reason": "file deleted"}))
            else:
                tool = "Write" if kind_type == "add" else "Edit"
                calls.append(Call(tool, {"file_path": path}, f"{item_id}:{i}", sid, cwd))
        return calls
    if kind == "McpToolCall":
        args = item.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"arguments": args}
        return [Call(f"mcp__{item.get('server')}__{item.get('tool')}", args if isinstance(args, dict) else {},
                     item_id, sid, cwd)]
    if kind == "Extension" and item.get("savedPath"):
        return [Call("Write", {"file_path": item["savedPath"]}, item_id, sid, cwd,
                     {"reason": f"generated by {item.get('kind') or 'extension'}"})]
    return []


SHELL_WRAPPERS = {"sh", "bash", "zsh", "dash", "fish"}


def _command_text(command) -> str:
    """Codex stores commands as argv lists, usually `/bin/zsh -lc '<command>'`.

    Unwrap that so the real command is what gets classified and shown."""
    if isinstance(command, list):
        argv = [str(part) for part in command]
        if (len(argv) >= 3 and argv[0].rsplit("/", 1)[-1] in SHELL_WRAPPERS
                and argv[1].startswith("-") and "c" in argv[1]):
            return " ".join([argv[2]] + argv[3:])
        return shlex.join(argv)
    return "" if command is None else str(command)


def _codex_agent(_entry: dict, meta: dict) -> str:
    origin = meta.get("originator") or meta.get("source")
    return f"codex ({origin})" if origin else "codex"


def _inbox_calls(entry: dict, _meta: dict) -> list:
    """A receipt line another agent wrote itself. See docs/RECEIPT_LINE.md."""
    action = entry.get("action")
    if not entry.get("agent") or action not in ACTION_TYPES:
        return []
    detail = entry.get("detail") if isinstance(entry.get("detail"), dict) else {}
    reversible = entry.get("reversible")
    overrides = {
        "action_type": action,
        "target": entry.get("target"),
        "artifact_link": entry.get("link"),
        "amount": entry.get("amount"),
        "currency": entry.get("currency"),
        "agent": str(entry["agent"]),
    }
    if reversible is True or reversible is False:
        overrides["reversible"] = int(reversible)
        overrides["reason"] = entry.get("reason") or "declared by the agent"
    call_id = entry.get("id") or hashlib.sha256(
        json.dumps(entry, sort_keys=True).encode()).hexdigest()[:32]
    return [Call(f"declared:{action}", detail, str(call_id), entry.get("session"), entry.get("cwd"), overrides)]


CURSOR_ROOT = Path.home() / ".cursor" / "projects"
CURSOR_STATE_DB = Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
CURSOR_WRITE_TOOLS = {"Write": "Write", "Edit": "Edit", "StrReplace": "Edit", "MultiEdit": "Edit", "CreateFile": "Write",
                      "EditFile": "Edit", "edit_file_v2": "Edit", "write_file": "Write", "search_replace": "Edit"}
CURSOR_SHELL_TOOLS = {"Shell", "Bash", "RunTerminalCommand", "run_terminal_command_v2", "run_terminal_cmd"}
CURSOR_DELETE_TOOLS = {"Delete", "DeleteFile", "delete_file"}
CURSOR_READ_TOOLS = {"Read", "ReadFile", "read_file", "Grep", "grep", "Glob", "glob", "LS", "ls", "ListDir", "list_dir",
                     "Search", "SemanticSearch", "codebase_search", "WebSearch", "web_search", "Fetch", "Todo", "TodoWrite",
                     "todo_write", "Plan", "Think", "Task", "ReadLints", "read_lints", "Diagnostics"}
CURSOR_STAMP = re.compile(r"<timestamp>[^<]*?([A-Z][a-z]{2}) (\d{1,2}), (\d{4}), (\d{1,2}):(\d{2}) (AM|PM) \(UTC([+-]\d{1,2})(?::?(\d{2}))?\)")
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def _cursor_unsanitize(name: str) -> str:
    """`Users-marc-Desktop-cursor-test` -> `/Users/marc/Desktop/cursor-test`, checking the disk so
    hyphens inside a folder name survive (longest existing prefix wins)."""
    tokens = name.split("-")
    path, i = Path("/"), 0
    while i < len(tokens):
        for j in range(len(tokens), i, -1):
            cand = path / "-".join(tokens[i:j])
            if cand.exists():
                path, i = cand, j
                break
        else:
            path, i = path / tokens[i], i + 1
    return str(path)


def _cursor_meta(path: Path) -> dict:
    """Per-transcript facts: the folder it ran in, and (from Cursor's state db) the model and title."""
    # "conversation_title" on purpose, not "title": the project label must be the folder, not the chat's name.
    meta = {"conversation_id": path.stem, "cwd": None, "model": None, "conversation_title": None, "mtime": None}
    try:
        project_dir = path.parent.parent.parent.name          # <project>/agent-transcripts/<id>/<id>.jsonl
        if project_dir and project_dir != "empty-window":
            meta["cwd"] = _cursor_unsanitize(project_dir)
        meta["mtime"] = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    except OSError:
        pass
    db = CURSOR_STATE_DB
    if db.exists():
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            try:
                row = conn.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (f"composerData:{path.stem}",)).fetchone()
            finally:
                conn.close()
            if row:
                data = json.loads(row[0])
                meta["conversation_title"] = data.get("name")
                meta["model"] = ((data.get("modelConfig") or {}).get("modelName")) or None
        except (sqlite3.Error, ValueError, OSError):
            pass
    return meta


def _cursor_stamp(text: str):
    m = CURSOR_STAMP.search(text or "")
    if not m:
        return None
    mon, day, year, hour, minute, ampm, off_h, off_m = m.groups()
    hour = int(hour) % 12 + (12 if ampm == "PM" else 0)
    sign = "-" if off_h.startswith("-") else "+"
    return f"{int(year):04d}-{MONTHS[mon]:02d}-{int(day):02d}T{hour:02d}:{int(minute):02d}:00{sign}{abs(int(off_h)):02d}:{int(off_m or 0):02d}"


def _cursor_calls(entry: dict, meta: dict) -> list:
    """Cursor agent transcripts: {"role": ..., "message": {"content": [blocks]}}; user turns carry a
    <timestamp> tag that dates the assistant's tool calls that follow (minute precision)."""
    message = entry.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        return []
    if entry.get("role") == "user":
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                stamp = _cursor_stamp(block.get("text"))
                if stamp:
                    meta["_ts"] = stamp
        return []
    if entry.get("role") != "assistant":
        return []
    entry["_ts"] = meta.get("_ts") or meta.get("mtime")
    calls = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name") or "")
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        path = inp.get("path") or inp.get("file_path") or inp.get("relativeWorkspacePath") or inp.get("target_file")
        if path and not str(path).startswith("/") and meta.get("cwd"):
            path = str(Path(meta["cwd"]) / str(path))
        if name in CURSOR_WRITE_TOOLS:
            calls.append(Call(CURSOR_WRITE_TOOLS[name], {"file_path": path, **{k: v for k, v in inp.items() if k != "contents"}},
                              block.get("id"), meta.get("conversation_id"), meta.get("cwd")))
        elif name in CURSOR_DELETE_TOOLS:
            calls.append(Call("cursor:delete", {"file_path": path}, block.get("id"), meta.get("conversation_id"), meta.get("cwd")))
        elif name in CURSOR_SHELL_TOOLS:
            calls.append(Call("Bash", {"command": inp.get("command"), "description": inp.get("description")},
                              block.get("id"), meta.get("conversation_id"), meta.get("cwd")))
        elif name in CURSOR_READ_TOOLS or not name:
            continue
        else:
            calls.append(Call(f"cursor:{name}", inp, block.get("id"), meta.get("conversation_id"), meta.get("cwd")))
    return calls


def _cursor_agent(_entry: dict, meta: dict) -> str:
    return f"cursor ({meta['model']})" if meta.get("model") else "cursor"


CLAUDE_CODE = Source(
    name="claude-code",
    root=Path.home() / ".claude" / "projects",
    pattern="*.jsonl",
    extract=_tool_use_calls,
    agent_name=_claude_code_agent,
    timestamp=lambda e: e.get("timestamp"),
    session_meta=_claude_code_meta,
    line_hint='"tool_use"',
)
COWORK = Source(
    name="cowork",
    root=Path.home() / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions",
    pattern="audit.jsonl",
    extract=_tool_use_calls,
    agent_name=_cowork_agent,
    timestamp=lambda e: e.get("_audit_timestamp") or e.get("timestamp"),
    session_meta=_cowork_meta,
    line_hint='"tool_use"',
)
CODEX = Source(
    name="codex",
    root=Path.home() / ".codex" / "sessions",
    pattern="*.jsonl",
    extract=_codex_calls,
    agent_name=_codex_agent,
    timestamp=lambda e: e.get("timestamp"),
    session_meta=_codex_meta,
    line_hint='"item_completed"',
)
CURSOR = Source(
    name="cursor",
    root=CURSOR_ROOT,
    pattern="agent-transcripts/*/*.jsonl",
    extract=_cursor_calls,
    agent_name=_cursor_agent,
    timestamp=lambda e: e.get("_ts"),
    session_meta=_cursor_meta,
    line_hint='"message"',
)
INBOX = Source(
    name="inbox",
    root=Path.home() / ".agent-receipt" / "inbox",
    pattern="*.jsonl",
    extract=_inbox_calls,
    agent_name=lambda e, _m: str(e.get("agent") or "unknown agent"),
    timestamp=lambda e: e.get("ts") or e.get("timestamp"),
    session_meta=lambda _p: {},
)
SOURCES = (CLAUDE_CODE, COWORK, CODEX, CURSOR, INBOX)


def current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


# --- classification ----------------------------------------------------------

def classify(name: str, tool_input: dict):
    """Return (action_type, target, artifact_link) or None if not a side effect."""
    if name.startswith("declared:"):
        action = name.split(":", 1)[1]
        return (action if action in ACTION_TYPES else "other"), None, None
    if name in FILE_WRITE_TOOLS or name in ("codex:file_delete", "cursor:delete"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return "file_write", path, (f"file://{path}" if path else None)
    if name.startswith("cursor:"):
        # Any other Cursor tool that is not a read: recorded as "other" so nothing is silently missed.
        target = next((str(v) for k in ("description", "url", "query", "text", "message", "path", "command")
                       for v in [tool_input.get(k)] if v), None)
        return "other", _clip(target), None
    if name in EXECUTE_TOOLS or (name.startswith("mcp__") and name.split("__", 2)[-1] in MCP_SHELL_TOOLS):
        command = _command_text(tool_input.get("command"))
        if is_read_only(command):
            return None
        return "execute", _clip(command), None
    if name in POST_TOOLS:
        files = tool_input.get("files")
        return "post", _clip(", ".join(files) if isinstance(files, list) else files), None
    if name == "Artifact":
        if tool_input.get("action") not in ARTIFACT_WRITE_ACTIONS:
            return None
        target = tool_input.get("url") or tool_input.get("file_path")
        return "post", target, tool_input.get("url")
    if name == "Agent":
        # Spawning a sub-agent is recorded so the worker's own rows have a parent on the receipt.
        role = tool_input.get("subagent_type") or "agent"
        task = " ".join(str(tool_input.get("description") or "").split())
        return "other", _clip(f"{role}: {task}" if task else role), None
    if name.startswith("mcp__"):
        tool = name.split("__", 2)[-1]
        if tool in UI_ONLY_MCP_TOOLS or tool.startswith(MCP_READ_PREFIXES):
            return None
        effects = _browser_side_effects(tool, tool_input)
        if effects is not None:
            if not effects:
                return None
            return "other", _clip(", ".join(dict.fromkeys(effects))), None
        target = (tool_input.get("to") or tool_input.get("recipient") or tool_input.get("url")
                  or tool_input.get("file_path") or tool_input.get("title") or tool_input.get("text")
                  or tool_input.get("message"))
        for action_type, pattern in MCP_TYPE_PATTERNS:
            if re.match(pattern, tool):
                return action_type, _clip(target), None
        return "other", _clip(target), None
    return None


def _browser_side_effects(tool: str, tool_input: dict):
    """For browser tools, the inner actions that change something ([] = none).
    None means this is not a browser tool."""
    if tool == "computer":
        action = tool_input.get("action")
        return [] if action in BROWSER_READ_ACTIONS else [action or "computer"]
    if tool == "browser_batch":
        effects = []
        for item in tool_input.get("actions") or []:
            if not isinstance(item, dict):
                continue
            inner_name = item.get("name") or "?"
            inner = item.get("input") if isinstance(item.get("input"), dict) else {}
            if inner_name == "computer":
                action = inner.get("action")
                if action not in BROWSER_READ_ACTIONS:
                    effects.append(action or "computer")
            elif inner_name not in BROWSER_READ_TOOLS:
                effects.append(inner_name)
        return effects
    return None


def _clip(value):
    if value is None:
        return None
    value = str(value)
    return value if len(value) <= TARGET_MAX_CHARS else value[:TARGET_MAX_CHARS] + "…"


def _cap_strings(obj):
    if isinstance(obj, str) and len(obj) > RAW_STRING_MAX_CHARS:
        return obj[:RAW_STRING_MAX_CHARS] + f"… [{len(obj)} chars total]"
    if isinstance(obj, dict):
        return {k: _cap_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_cap_strings(v) for v in obj]
    return obj


def _to_epoch(iso) -> float | None:
    if isinstance(iso, (int, float)):
        return float(iso) / (1000.0 if iso > 1e11 else 1.0)
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _base_note(source_name: str, session_id, reason, declared=False) -> str:
    if declared:
        note = f"declared by the agent itself via the receipt inbox, session {session_id}"
    else:
        where = {"claude-code": "Claude Code transcript", "cursor": "Cursor transcript (time from the turn's stamp, minute precision)"}.get(source_name, f"{source_name} transcript")
        note = f"declared in {where}, session {session_id}"
    return f"{note}; reversibility: {reason}" if reason else note


def build_action(call: Call, source_name: str, timestamp: float, agent: str, user: str,
                 path: Path | None, entry: dict | None = None, meta: dict | None = None) -> dict | None:
    """Turn a Call into an `actions` row dict, or None if it is not a side effect."""
    classified = classify(call.name, call.input)
    if classified is None:
        return None
    action_type, target, artifact_link = classified
    o = call.overrides
    action_type = o.get("action_type", action_type)
    target = _clip(o["target"]) if "target" in o and o["target"] is not None else target
    artifact_link = o.get("artifact_link", artifact_link)
    reversible, reason = assess(action_type, call.name, call.input, target, call.cwd)
    if "reversible" in o:
        reversible, reason = o["reversible"], o.get("reason") or reason
    elif o.get("reason"):
        reason = f"{o['reason']}; {reason}" if reason else o["reason"]
    entry = entry or {}
    meta = meta or {}
    return {
        "timestamp": timestamp,
        "agent": o.get("agent", agent),
        "user": user,
        "source": "log",
        "action_type": action_type,
        "target": target,
        "amount": o.get("amount"),
        "currency": o.get("currency"),
        "artifact_link": artifact_link,
        "reversible": reversible,
        "attribution": "agent",
        "confidence_note": _base_note(source_name, call.session_id, reason, source_name == "inbox"),
        "raw_json": json.dumps({
            "tool": call.name,
            "input": _cap_strings(call.input),
            "overrides": _cap_strings(o),
            "session_id": call.session_id,
            "cwd": call.cwd,
            "project": (entry.get("project") if source_name == "inbox" else None),
            "transcript": str(path) if path else None,
            "source": source_name,
            "entrypoint": entry.get("entrypoint"),
            "sidechain": bool(entry.get("isSidechain")),
            "agent_id": entry.get("agentId"),
            "parent_session": meta.get("parent_session"),
            "spawned_by": meta.get("toolUseId"),
            "version": entry.get("version"),
        }),
        "source_ref": call.call_id,
    }


# --- parsing -----------------------------------------------------------------

def parse_lines(path: Path, lines, source: Source = CLAUDE_CODE):
    """Yield one action dict per side effect. `lines` is (position, text)."""
    user = current_user()
    meta = source.session_meta(path)
    for pos, line in lines:
        # Cheap prefilter: in multi-GB transcripts most lines are tool results or chat.
        if source.line_hint and source.line_hint not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        calls = source.extract(entry, meta)
        if not calls:
            continue
        timestamp = _to_epoch(source.timestamp(entry))
        if timestamp is None:
            continue
        agent = source.agent_name(entry, meta)
        project = meta.get("title")
        for idx, call in enumerate(calls):
            call.call_id = call.call_id or f"{path.name}:{pos}:{idx}"
            action = build_action(call, source.name, timestamp, agent, user, path, entry, meta)
            if action is None:
                continue
            if project:
                raw = json.loads(action["raw_json"])
                raw["project"] = project
                action["raw_json"] = json.dumps(raw)
            yield action


def parse_file(path: Path, source: Source = CLAUDE_CODE):
    with open(path, encoding="utf-8", errors="replace") as f:
        yield from parse_lines(path, enumerate(f, 1), source)


class _NewLines:
    """Streams complete lines appended since `offset`; `.offset` ends past the last one.

    Also hashes every byte it yields (`.digest`), so the consumed range can be
    recorded and re-verified later."""

    def __init__(self, path: Path, offset: int):
        size = path.stat().st_size
        self.path = path
        self.start = self.offset = 0 if size < offset else offset
        self._hash = hashlib.sha256()

    @property
    def digest(self) -> str:
        return self._hash.hexdigest()

    def __iter__(self):
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            while True:
                raw = f.readline()
                if not raw or not raw.endswith(b"\n"):
                    return
                pos = self.offset
                self.offset += len(raw)
                self._hash.update(raw)
                yield pos, raw.decode("utf-8", errors="replace")


def find_transcripts(root=None, source: Source = CLAUDE_CODE):
    root = Path(root) if root else source.root
    if not root.exists():
        return []
    return sorted(p for p in root.rglob(source.pattern) if p.is_file())


INSERT_SQL = """
INSERT OR IGNORE INTO actions
    (timestamp, agent, user, source, action_type, target, amount, currency,
     artifact_link, reversible, attribution, confidence_note, raw_json, source_ref)
VALUES
    (:timestamp, :agent, :user, :source, :action_type, :target, :amount, :currency,
     :artifact_link, :reversible, :attribution, :confidence_note, :raw_json, :source_ref)
"""


def _rules_changed(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key = 'rules_version'").fetchone()
    return row is None or row[0] != RULES_VERSION


def _reset_if_rules_changed(conn: sqlite3.Connection, full: bool) -> None:
    if full or _rules_changed(conn):
        # Only rows re-creatable from a transcript are dropped. Rows attributed by credential
        # (the agent's own Google account, its Ramp fund, its GitHub account) are also
        # source='log' but have no transcript behind them: deleting them would lose them.
        names = tuple(s.name for s in SOURCES)
        marks = ",".join("?" * len(names))
        where = f"source = 'log' AND COALESCE(json_extract(raw_json, '$.source'), 'claude-code') IN ({marks})"
        conn.execute(f"DELETE FROM alerts WHERE action_id IN (SELECT id FROM actions WHERE {where})", names)
        conn.execute("DELETE FROM meta WHERE key = 'alerts_evaluated_to'")
        conn.execute(f"DELETE FROM actions WHERE {where}", names)
        conn.execute("DELETE FROM parser_state")
        conn.execute("DELETE FROM transcript_chunks")
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('rules_version', ?)",
                     (RULES_VERSION,))
        conn.commit()


def ingest(conn: sqlite3.Connection, paths=None, full: bool = False,
           source: Source = CLAUDE_CODE) -> int:
    """Parse what's new in each transcript of one source. Returns how many actions were new."""
    if paths is None:
        paths = find_transcripts(source=source)
    _reset_if_rules_changed(conn, full)
    inserted = 0
    for path in paths:
        row = conn.execute("SELECT byte_offset FROM parser_state WHERE path = ?", (str(path),)).fetchone()
        reader = _NewLines(path, row[0] if row else 0)
        if reader.start == 0:
            # Re-reading from the top (new file, or it shrank): old chunks no longer apply.
            conn.execute("DELETE FROM transcript_chunks WHERE path = ?", (str(path),))
        new_rows = 0
        try:
            for action in parse_lines(path, reader, source):
                if conn.execute(INSERT_SQL, action).rowcount:
                    new_rows += 1
                elif source.name == "inbox":
                    _merge_declaration(conn, action)
        except Exception as exc:  # one bad transcript must not stop the receipt
            conn.rollback()
            print(f"[{source.name}] could not parse {path.name}: {exc!r}; will retry next refresh",
                  file=sys.stderr)
            continue
        inserted += new_rows
        if reader.offset > reader.start:
            conn.execute(
                "INSERT INTO transcript_chunks (path, start_offset, end_offset, sha256, first_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(path), reader.start, reader.offset, reader.digest, time.time()))
        conn.execute("INSERT OR REPLACE INTO parser_state (path, byte_offset) VALUES (?, ?)",
                     (str(path), reader.offset))
        conn.commit()
    return inserted


def _merge_declaration(conn: sqlite3.Connection, action: dict) -> None:
    """A receipt line whose id matches an action already observed elsewhere
    (e.g. the same message seen in the mailbox) is the same action: the
    declaration wins on attribution, the observation stays on record."""
    row = conn.execute(
        "SELECT id, source, confidence_note FROM actions WHERE source_ref = ? AND source != 'log'",
        (action["source_ref"],),
    ).fetchone()
    if not row:
        return
    action_id, observed_source, note = row
    base = action["confidence_note"] + f"; also observed via {observed_source}"
    tail = (note or "").split(" | ", 1)
    new_note = base + (" | " + tail[1] if len(tail) > 1 else "")
    conn.execute(
        "UPDATE actions SET source = 'log', agent = ?, attribution = 'agent', confidence_note = ?, "
        "reversible = COALESCE(?, reversible), raw_json = ? WHERE id = ?",
        (action["agent"], new_note, action["reversible"], action["raw_json"], action_id),
    )


def ingest_all(conn: sqlite3.Connection, sources=SOURCES, full: bool = False) -> int:
    _reset_if_rules_changed(conn, full)
    return sum(ingest(conn, source=source) for source in sources)


def reconcile(conn: sqlite3.Connection):
    """Apply current rules to rows already in the database.

    Deletes rows no longer counted as side effects and refreshes reversibility.
    Rows whose stored input was truncated are left alone: we can't re-judge them.
    Returns (removed, updated)."""
    rows = conn.execute(
        "SELECT id, raw_json, reversible, confidence_note, timestamp, agent, user FROM actions WHERE source = 'log'"
    ).fetchall()
    doomed, updates = [], []
    for action_id, raw, reversible, note, timestamp, agent, user in rows:
        try:
            data = json.loads(raw or "")
        except ValueError:
            continue
        tool_input = data.get("input") if isinstance(data.get("input"), dict) else {}
        if "chars total]" in json.dumps(tool_input):
            continue
        # Only rows that came from a transcript source are re-judged here. Rows
        # attributed by credential (e.g. the agent's own Google account) carry
        # source='log' too but were never classified from a tool call.
        if data.get("source") is not None and data.get("source") not in {s.name for s in SOURCES}:
            continue
        call = Call(data.get("tool") or "", tool_input, None, data.get("session_id"), data.get("cwd"),
                    data.get("overrides") if isinstance(data.get("overrides"), dict) else {})
        rebuilt = build_action(call, data.get("source") or "claude-code", timestamp, agent, user, None)
        if rebuilt is None:
            doomed.append((action_id,))
            continue
        rest = (note or "").split(" | ", 1)
        new_note = rebuilt["confidence_note"] + (" | " + rest[1] if len(rest) > 1 else "")
        if rebuilt["reversible"] != reversible or new_note != note:
            updates.append((rebuilt["reversible"], new_note, action_id))
    conn.executemany("DELETE FROM actions WHERE id = ?", doomed)
    conn.executemany("UPDATE actions SET reversible = ?, confidence_note = ? WHERE id = ?", updates)
    conn.execute("UPDATE actions SET user = ? WHERE user IS NULL OR user = ''", (current_user(),))
    conn.commit()
    return len(doomed), len(updates)


def run(db_path: Path = DB_PATH, full: bool = False) -> None:
    conn = connect(db_path)
    removed, updated = reconcile(conn)
    inserted = ingest_all(conn, full=full)
    total = conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
    for source in SOURCES:
        print(f"{source.name}: {len(find_transcripts(source=source))} transcript(s) under {source.root}")
    print(f"Reconciled existing rows: {removed} removed, {updated} updated")
    print(f"Inserted {inserted} new action(s); {total} total in {db_path}")
    conn.close()


if __name__ == "__main__":
    import sys
    run(full="--full" in sys.argv)

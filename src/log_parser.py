"""Log parser: side-effect tool calls from agent transcripts -> `actions`.

Sources (documented in docs/SOURCES.md):
- Claude Code: ~/.claude/projects/**/*.jsonl
- Cowork (Claude Desktop's local agent mode): local-agent-mode-sessions/**/audit.jsonl

Reads are skipped on purpose. Incremental: a byte offset per transcript is kept
in `parser_state`, so a refresh only parses what was appended. Re-running is
always safe: each tool call's own id is stored as `source_ref` (UNIQUE).
"""

import getpass
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from reversibility import assess
from shell_classify import is_read_only
from store import DB_PATH, connect

# Bumping this deletes every log-sourced row and re-extracts all transcripts.
# Do it whenever what we extract, or how we judge it, changes.
RULES_VERSION = "6"

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
# MCP tools whose name says what kind of side effect they are. Checked after the
# read/UI filters, by regular expression on the part after the server prefix.
MCP_TYPE_PATTERNS = (
    ("send_email", r"^(send_(e?mail|gmail)|gmail_send|mail_send|send_message_gmail)"),
    ("create_event", r"^(create_(calendar_)?event|calendar_create|add_event|schedule_meeting)"),
    ("purchase", r"^(purchase|place_order|checkout|create_(payment|charge|order)|pay$|buy)"),
    ("post", r"^(send_message|post_message|send_sms|send_text|reply|post$|publish|tweet)"),
)
# MCP tools that run a shell command (Cowork's sandbox exposes one).
MCP_SHELL_TOOLS = {"bash"}
# Browser automation: `computer` actions that only look, and batch items that only look.
BROWSER_READ_ACTIONS = {"screenshot", "zoom", "wait", "scroll", "scroll_to", "hover"}
BROWSER_READ_TOOLS = {"read_page", "find", "get_page_text", "read_console_messages",
                      "read_network_requests", "tabs_context", "tabs_context_mcp",
                      "tabs_select", "resize_window"}

TARGET_MAX_CHARS = 500
RAW_STRING_MAX_CHARS = 10_000


# --- sources -----------------------------------------------------------------

@dataclass(frozen=True)
class Source:
    name: str
    root: Path
    pattern: str
    agent_name: Callable[[dict, dict], str]      # (entry, session_meta) -> label
    timestamp: Callable[[dict], str | None]      # entry -> ISO string
    session_meta: Callable[[Path], dict]         # transcript path -> metadata dict


def _claude_code_agent(entry: dict, _meta: dict) -> str:
    entrypoint = entry.get("entrypoint")
    name = f"claude-code ({entrypoint})" if entrypoint else "claude-code"
    if entry.get("isSidechain"):
        name += " › subagent"
    return name


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


CLAUDE_CODE = Source(
    name="claude-code",
    root=Path.home() / ".claude" / "projects",
    pattern="*.jsonl",
    agent_name=_claude_code_agent,
    timestamp=lambda e: e.get("timestamp"),
    session_meta=lambda _p: {},
)
COWORK = Source(
    name="cowork",
    root=Path.home() / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions",
    pattern="audit.jsonl",
    agent_name=_cowork_agent,
    timestamp=lambda e: e.get("_audit_timestamp") or e.get("timestamp"),
    session_meta=_cowork_meta,
)
SOURCES = (CLAUDE_CODE, COWORK)


def current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


# --- classification ----------------------------------------------------------

def classify(name: str, tool_input: dict):
    """Return (action_type, target, artifact_link) or None if not a side effect."""
    if name in FILE_WRITE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return "file_write", path, (f"file://{path}" if path else None)
    if name in EXECUTE_TOOLS or (name.startswith("mcp__") and name.split("__", 2)[-1] in MCP_SHELL_TOOLS):
        command = tool_input.get("command") or ""
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


def _to_epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _base_note(source: Source, session_id, reason) -> str:
    where = "Claude Code transcript" if source.name == "claude-code" else f"{source.name} transcript"
    note = f"declared in {where}, session {session_id}"
    return f"{note}; reversibility: {reason}" if reason else note


# --- parsing -----------------------------------------------------------------

def parse_lines(path: Path, lines, source: Source = CLAUDE_CODE):
    """Yield one action dict per side-effect tool call. `lines` is (position, text)."""
    user = current_user()
    meta = source.session_meta(path)
    for pos, line in lines:
        # Cheap prefilter: only lines carrying a tool call can matter, and in
        # multi-GB transcripts most lines are tool results or chat.
        if '"tool_use"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        timestamp = source.timestamp(entry)
        if not timestamp:
            continue
        for idx, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name") or ""
            tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
            classified = classify(name, tool_input)
            if classified is None:
                continue
            action_type, target, artifact_link = classified
            session_id = entry.get("sessionId") or entry.get("session_id")
            cwd = entry.get("cwd") or meta.get("cwd")
            reversible, reason = assess(action_type, name, tool_input, target, cwd)
            yield {
                "timestamp": _to_epoch(timestamp),
                "agent": source.agent_name(entry, meta),
                "user": user,
                "source": "log",
                "action_type": action_type,
                "target": target,
                "amount": None,
                "currency": None,
                "artifact_link": artifact_link,
                "reversible": reversible,
                "attribution": "agent",
                "confidence_note": _base_note(source, session_id, reason),
                "raw_json": json.dumps({
                    "tool": name,
                    "input": _cap_strings(tool_input),
                    "session_id": session_id,
                    "cwd": cwd,
                    "project": meta.get("title"),
                    "transcript": str(path),
                    "source": source.name,
                    "entrypoint": entry.get("entrypoint"),
                    "sidechain": bool(entry.get("isSidechain")),
                    "version": entry.get("version"),
                }),
                "source_ref": block.get("id") or f"{path.name}:{pos}:{idx}",
            }


def parse_file(path: Path, source: Source = CLAUDE_CODE):
    with open(path, encoding="utf-8") as f:
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
    return sorted(root.rglob(source.pattern))


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
        conn.execute("DELETE FROM alerts WHERE action_id IN (SELECT id FROM actions WHERE source = 'log')")
        conn.execute("DELETE FROM meta WHERE key = 'alerts_evaluated_to'")
        conn.execute("DELETE FROM actions WHERE source = 'log'")
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
        before = conn.total_changes
        conn.executemany(INSERT_SQL, parse_lines(path, reader, source))
        inserted += conn.total_changes - before
        if reader.offset > reader.start:
            conn.execute(
                "INSERT INTO transcript_chunks (path, start_offset, end_offset, sha256, first_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(path), reader.start, reader.offset, reader.digest, time.time()))
        conn.execute("INSERT OR REPLACE INTO parser_state (path, byte_offset) VALUES (?, ?)",
                     (str(path), reader.offset))
        conn.commit()
    return inserted


def ingest_all(conn: sqlite3.Connection, sources=SOURCES, full: bool = False) -> int:
    _reset_if_rules_changed(conn, full)
    return sum(ingest(conn, source=source) for source in sources)


def reconcile(conn: sqlite3.Connection):
    """Apply current rules to rows already in the database.

    Deletes rows no longer counted as side effects and refreshes reversibility.
    Rows whose stored input was truncated are left alone: we can't re-judge them.
    Returns (removed, updated)."""
    rows = conn.execute(
        "SELECT id, raw_json, reversible, confidence_note FROM actions WHERE source = 'log'"
    ).fetchall()
    by_name = {s.name: s for s in SOURCES}
    doomed, updates = [], []
    for action_id, raw, reversible, note in rows:
        try:
            data = json.loads(raw or "")
        except ValueError:
            continue
        tool_input = data.get("input") if isinstance(data.get("input"), dict) else {}
        if "chars total]" in json.dumps(tool_input):
            continue
        tool = data.get("tool") or ""
        classified = classify(tool, tool_input)
        if classified is None:
            doomed.append((action_id,))
            continue
        action_type, target, _ = classified
        new_rev, reason = assess(action_type, tool, tool_input, target, data.get("cwd"))
        source = by_name.get(data.get("source"), CLAUDE_CODE)
        base = _base_note(source, data.get("session_id"), reason)
        rest = (note or "").split(" | ", 1)
        new_note = base + (" | " + rest[1] if len(rest) > 1 else "")
        if new_rev != reversible or new_note != note:
            updates.append((new_rev, new_note, action_id))
    conn.executemany("DELETE FROM actions WHERE id = ?", doomed)
    conn.executemany("UPDATE actions SET reversible = ?, confidence_note = ? WHERE id = ?", updates)
    conn.execute("UPDATE actions SET user = ? WHERE user IS NULL", (current_user(),))
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

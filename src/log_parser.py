"""Log parser: side-effect tool calls from Claude Code transcripts -> `actions`.

Source format is documented in docs/SOURCES.md. Reads are skipped on purpose.
Re-running is safe: each tool call's own id is stored as `source_ref` (UNIQUE),
so already-seen calls are ignored.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from shell_classify import is_read_only
from store import DB_PATH, connect

TRANSCRIPTS_ROOT = Path.home() / ".claude" / "projects"
AGENT_NAME = "claude-code"

FILE_WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
EXECUTE_TOOLS = {"Bash"}
POST_TOOLS = {"SendUserFile"}
# Artifact is a mixed tool: these `action` values change something; the rest read.
ARTIFACT_WRITE_ACTIONS = {None, "publish", "write_db", "upload_asset", "delete_asset",
                          "reply", "resolve", "delete", "pin", "unpin"}
# MCP tools are named mcp__<server>__<tool>. A tool whose name starts with one
# of these is a read and is skipped. Everything else under mcp__ is logged.
MCP_READ_PREFIXES = ("read", "get_", "list", "find", "search", "screenshot",
                     "tabs_context", "status", "preview_list", "preview_logs")
# MCP tools (by their name after the server prefix) that only change the chat
# or browser UI, not the world.
UI_ONLY_MCP_TOOLS = {"mark_chapter", "show_widget", "read_me", "tabs_select", "tabs_create",
                     "tabs_close", "tabs_create_mcp", "tabs_close_mcp", "resize_window"}
# Browser automation: `computer` actions that only look, and batch items that only look.
BROWSER_READ_ACTIONS = {"screenshot", "zoom", "wait", "scroll", "scroll_to", "hover"}
BROWSER_READ_TOOLS = {"read_page", "find", "get_page_text", "read_console_messages",
                      "read_network_requests", "tabs_context", "tabs_context_mcp",
                      "tabs_select", "resize_window"}

TARGET_MAX_CHARS = 500
RAW_STRING_MAX_CHARS = 10_000


def classify(name: str, tool_input: dict):
    """Return (action_type, target, artifact_link) or None if not a side effect."""
    if name in FILE_WRITE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return "file_write", path, (f"file://{path}" if path else None)
    if name in EXECUTE_TOOLS:
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
        target = tool_input.get("url") or tool_input.get("file_path") or tool_input.get("text")
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


def parse_file(path: Path):
    """Yield one action dict per side-effect tool call in a transcript."""
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
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
            for idx, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name") or ""
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                classified = classify(name, tool_input)
                if classified is None:
                    continue
                action_type, target, artifact_link = classified
                timestamp = entry.get("timestamp")
                if not timestamp:
                    continue
                session_id = entry.get("sessionId")
                yield {
                    "timestamp": _to_epoch(timestamp),
                    "agent": AGENT_NAME,
                    "source": "log",
                    "action_type": action_type,
                    "target": target,
                    "amount": None,
                    "currency": None,
                    "artifact_link": artifact_link,
                    "reversible": None,
                    "attribution": "agent",
                    "confidence_note": f"declared in Claude Code transcript, session {session_id}",
                    "raw_json": json.dumps({
                        "tool": name,
                        "input": _cap_strings(tool_input),
                        "session_id": session_id,
                        "cwd": entry.get("cwd"),
                        "transcript": str(path),
                    }),
                    "source_ref": block.get("id") or f"{path.name}:{line_no}:{idx}",
                }


def find_transcripts(root: Path = TRANSCRIPTS_ROOT):
    if not root.exists():
        return []
    return sorted(root.rglob("*.jsonl"))


INSERT_SQL = """
INSERT OR IGNORE INTO actions
    (timestamp, agent, source, action_type, target, amount, currency,
     artifact_link, reversible, attribution, confidence_note, raw_json, source_ref)
VALUES
    (:timestamp, :agent, :source, :action_type, :target, :amount, :currency,
     :artifact_link, :reversible, :attribution, :confidence_note, :raw_json, :source_ref)
"""


def ingest(conn: sqlite3.Connection, paths=None) -> int:
    """Parse every transcript and insert new actions. Returns how many were new."""
    if paths is None:
        paths = find_transcripts()
    before = conn.total_changes
    for path in paths:
        conn.executemany(INSERT_SQL, parse_file(path))
    conn.commit()
    return conn.total_changes - before


def prune(conn: sqlite3.Connection) -> int:
    """Delete log actions that the current rules no longer count as side effects.

    Rows whose stored input was truncated are kept: we can't re-judge them."""
    rows = conn.execute("SELECT id, raw_json FROM actions WHERE source = 'log'").fetchall()
    doomed = []
    for action_id, raw in rows:
        try:
            data = json.loads(raw or "")
        except ValueError:
            continue
        tool_input = data.get("input") if isinstance(data.get("input"), dict) else {}
        if "chars total]" in json.dumps(tool_input):
            continue
        if classify(data.get("tool") or "", tool_input) is None:
            doomed.append((action_id,))
    conn.executemany("DELETE FROM actions WHERE id = ?", doomed)
    conn.commit()
    return len(doomed)


def run(db_path: Path = DB_PATH) -> None:
    conn = connect(db_path)
    paths = find_transcripts()
    pruned = prune(conn)
    inserted = ingest(conn, paths)
    total = conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
    print(f"Scanned {len(paths)} transcript(s) under {TRANSCRIPTS_ROOT}")
    print(f"Removed {pruned} row(s) no longer counted as side effects")
    print(f"Inserted {inserted} new action(s); {total} total in {db_path}")
    conn.close()


if __name__ == "__main__":
    run()

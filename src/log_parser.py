"""Log parser: side-effect tool calls from Claude Code transcripts -> `actions`.

Source format is documented in docs/SOURCES.md. Reads are skipped on purpose.
Re-running is safe: each tool call's own id is stored as `source_ref` (UNIQUE),
so already-seen calls are ignored.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

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
# Named MCP tools that only change the chat UI, not the world.
MCP_NOT_SIDE_EFFECTS = {
    "mcp__ccd_session__mark_chapter",
    "mcp__visualize__show_widget",
    "mcp__Claude_Browser__tabs_select",
}

TARGET_MAX_CHARS = 500
RAW_STRING_MAX_CHARS = 10_000


def classify(name: str, tool_input: dict):
    """Return (action_type, target, artifact_link) or None if not a side effect."""
    if name in FILE_WRITE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return "file_write", path, (f"file://{path}" if path else None)
    if name in EXECUTE_TOOLS:
        return "execute", _clip(tool_input.get("command")), None
    if name in POST_TOOLS:
        files = tool_input.get("files")
        return "post", _clip(", ".join(files) if isinstance(files, list) else files), None
    if name == "Artifact":
        if tool_input.get("action") not in ARTIFACT_WRITE_ACTIONS:
            return None
        target = tool_input.get("url") or tool_input.get("file_path")
        return "post", target, tool_input.get("url")
    if name.startswith("mcp__"):
        if name in MCP_NOT_SIDE_EFFECTS:
            return None
        tool = name.split("__", 2)[-1]
        if tool.startswith(MCP_READ_PREFIXES):
            return None
        target = tool_input.get("url") or tool_input.get("file_path") or tool_input.get("text")
        return "other", _clip(target), None
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


def run(db_path: Path = DB_PATH) -> None:
    conn = connect(db_path)
    paths = find_transcripts()
    inserted = ingest(conn, paths)
    total = conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
    print(f"Scanned {len(paths)} transcript(s) under {TRANSCRIPTS_ROOT}")
    print(f"Inserted {inserted} new action(s); {total} total in {db_path}")
    conn.close()


if __name__ == "__main__":
    run()

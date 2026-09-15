"""Correlator: applies the attribution rules from CLAUDE.md to every action.

- Agent log entry at time T -> `agent`. The log is a declaration, not an inference,
  so it wins even when physical input was also present (that gets a note).
- Physical input in the WINDOW seconds before a non-log action, and no agent log
  entry in that window -> `human`.
- Neither -> `unknown`.
- If the input monitor was not running at the time, we cannot tell "no input"
  from "not looking", so non-log actions become `unknown` and log actions say so.

Safe to re-run: the correlation part of confidence_note is rebuilt each time.
"""

import sqlite3
from pathlib import Path

from store import DB_PATH, connect

WINDOW_SECONDS = 30
NOTE_SEPARATOR = " | "


def is_input_covered(conn: sqlite3.Connection, ts: float) -> bool:
    row = conn.execute(
        "SELECT 1 FROM coverage WHERE source = 'input' AND started_at <= ? AND ended_at >= ? LIMIT 1",
        (ts, ts),
    ).fetchone()
    return row is not None


def count_input_before(conn: sqlite3.Connection, ts: float, window: float) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM input_events WHERE timestamp > ? AND timestamp <= ?",
        (ts - window, ts),
    ).fetchone()[0]


def has_log_action_before(conn: sqlite3.Connection, ts: float, window: float, exclude_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM actions WHERE source = 'log' AND id != ? "
        "AND timestamp > ? AND timestamp <= ? LIMIT 1",
        (exclude_id, ts - window, ts),
    ).fetchone()
    return row is not None


def attribute(source: str, covered: bool, input_count: int, log_match: bool, window: float):
    """Return (attribution, note). Pure function of the evidence; no guessing."""
    if source == "log":
        if input_count > 0:
            return "agent", f"agent log wins; physical input also present ({input_count} events in prior {window:.0f}s)"
        if covered:
            return "agent", f"agent log; no physical input in prior {window:.0f}s"
        return "agent", "agent log; input monitor was not running at the time"
    if not covered:
        return "unknown", "input monitor was not running at the time"
    if log_match:
        return "agent", f"matching agent log entry within {window:.0f}s"
    if input_count > 0:
        return "human", f"physical input present ({input_count} events in prior {window:.0f}s), no agent log entry"
    return "unknown", f"no physical input and no agent log entry in prior {window:.0f}s"


def correlate(conn: sqlite3.Connection, window: float = WINDOW_SECONDS) -> dict:
    """Re-attribute every action. Returns counts by attribution."""
    rows = conn.execute("SELECT id, timestamp, source, confidence_note, attribution FROM actions").fetchall()
    counts = {"agent": 0, "human": 0, "unknown": 0}
    for action_id, ts, source, note, stored in rows:
        if source == "input":
            # The app's own buttons (retire / restore an agent): a person pressed them.
            attribution, reason = "human", "a button in Agent Receipt was pressed"
        elif source != "log" and (note or "").startswith("by credential:"):
            # Attributed by who owns the credential (a named person's Ramp card): keyboard
            # timing adds nothing, so the connector's verdict stands.
            attribution, reason = stored, "attributed by the credential's owner, not by keyboard timing"
        else:
            covered = is_input_covered(conn, ts)
            input_count = count_input_before(conn, ts, window)
            log_match = source != "log" and has_log_action_before(conn, ts, window, action_id)
            attribution, reason = attribute(source, covered, input_count, log_match, window)
        base = (note or "").split(NOTE_SEPARATOR)[0]
        new_note = f"{base}{NOTE_SEPARATOR}{reason}" if base else reason
        conn.execute(
            "UPDATE actions SET attribution = ?, confidence_note = ? WHERE id = ?",
            (attribution, new_note, action_id),
        )
        counts[attribution] += 1
    conn.commit()
    return counts


def run(db_path: Path = DB_PATH, window: float = WINDOW_SECONDS) -> None:
    conn = connect(db_path)
    counts = correlate(conn, window)
    uncovered = conn.execute(
        "SELECT COUNT(*) FROM actions WHERE confidence_note LIKE '%monitor was not running%'"
    ).fetchone()[0]
    total = sum(counts.values())
    print(f"Correlated {total} action(s) with a {window:.0f}s window")
    print(f"  agent: {counts['agent']}  human: {counts['human']}  unknown: {counts['unknown']}")
    print(f"  {uncovered} of {total} happened while the input monitor was not running")
    conn.close()


if __name__ == "__main__":
    run()

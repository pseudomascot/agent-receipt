"""Read-side queries shared by the statement page and the daily summary."""

import sqlite3
from datetime import date, datetime, timedelta

# What the receipt can and cannot see. Shown on every page so gaps are never silent.
COVERAGE_NOTES = [
    ("Claude Code sessions (CLI and the desktop app's Code tab)", "covered",
     "Read from Claude Code's own transcripts. Every side-effect tool call is listed."),
    ("Physical keyboard/mouse activity", "partial",
     "Only while the input monitor is running; see the monitor intervals for each day."),
    ("Claude Desktop chat (not the Code tab)", "not covered",
     "Keeps no usable local log; its actions do not appear here."),
    ("Email, card, and wallet activity", "not covered",
     "Planned for v2."),
]

NOT_COVERED = [name for name, status, _ in COVERAGE_NOTES if status == "not covered"]


def day_bounds(day: date):
    start = datetime(day.year, day.month, day.day).timestamp()
    end = (datetime(day.year, day.month, day.day) + timedelta(days=1)).timestamp()
    return start, end


def list_days(conn: sqlite3.Connection):
    rows = conn.execute(
        "SELECT date(timestamp, 'unixepoch', 'localtime') AS d, attribution, COUNT(*) "
        "FROM actions GROUP BY d, attribution ORDER BY d DESC"
    ).fetchall()
    days = {}
    for d, attribution, n in rows:
        entry = days.setdefault(d, {"day": d, "total": 0, "agent": 0, "human": 0, "unknown": 0})
        entry[attribution] += n
        entry["total"] += n
    return list(days.values())


def day_statement(conn: sqlite3.Connection, day: date):
    start, end = day_bounds(day)
    rows = conn.execute(
        "SELECT id, timestamp, agent, action_type, target, amount, currency, artifact_link, "
        "reversible, attribution, confidence_note "
        "FROM actions WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (start, end),
    ).fetchall()
    actions = []
    for r in rows:
        actions.append({
            "id": r[0],
            "time": datetime.fromtimestamp(r[1]).strftime("%H:%M:%S"),
            "agent": r[2],
            "action_type": r[3],
            "target": r[4] or "",
            "amount": r[5],
            "currency": r[6],
            "artifact_link": r[7],
            "reversible": {None: "not assessed", 0: "no", 1: "yes"}.get(r[8], "not assessed"),
            "attribution": r[9],
            "note": r[10] or "",
        })

    by_type = {}
    by_attribution = {"agent": 0, "human": 0, "unknown": 0}
    money = {}
    file_targets = {}
    uncovered = 0
    for a in actions:
        by_type[a["action_type"]] = by_type.get(a["action_type"], 0) + 1
        by_attribution[a["attribution"]] += 1
        if a["amount"] is not None:
            cur = a["currency"] or "?"
            money[cur] = money.get(cur, 0) + a["amount"]
        if a["action_type"] == "file_write" and a["target"]:
            file_targets[a["target"]] = file_targets.get(a["target"], 0) + 1
        if "monitor was not running" in a["note"]:
            uncovered += 1

    intervals = conn.execute(
        "SELECT started_at, ended_at FROM coverage WHERE source = 'input' "
        "AND ended_at >= ? AND started_at < ? ORDER BY started_at",
        (start, end),
    ).fetchall()
    monitor_intervals = []
    covered_seconds = 0.0
    for s, e in intervals:
        s, e = max(s, start), min(e, end)
        covered_seconds += e - s
        monitor_intervals.append((datetime.fromtimestamp(s).strftime("%H:%M"),
                                  datetime.fromtimestamp(e).strftime("%H:%M")))

    return {
        "day": day.isoformat(),
        "actions": [a for a in actions if a["attribution"] != "unknown"],
        "unknown": [a for a in actions if a["attribution"] == "unknown"],
        "total": len(actions),
        "by_type": sorted(by_type.items(), key=lambda kv: -kv[1]),
        "by_attribution": by_attribution,
        "money": money,
        "top_files": sorted(file_targets.items(), key=lambda kv: -kv[1])[:5],
        "uncovered": uncovered,
        "monitor_intervals": monitor_intervals,
        "monitor_minutes": round(covered_seconds / 60),
        "prev_day": (day - timedelta(days=1)).isoformat(),
        "next_day": (day + timedelta(days=1)).isoformat(),
    }

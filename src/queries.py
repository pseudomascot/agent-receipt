"""Read-side queries shared by the statement page and the daily summary."""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

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
ACTION_TYPES = ("send_email", "create_event", "purchase", "file_write", "post", "execute", "other")

# A leading `cd "<dir>" && ` says where, which the Project column already shows.
LEADING_CD = re.compile(r'^cd\s+("[^"]*"|\'[^\']*\'|\S+)\s*&&\s*')
SHORT_TARGET = 110


def day_bounds(day: date):
    start = datetime(day.year, day.month, day.day).timestamp()
    end = (datetime(day.year, day.month, day.day) + timedelta(days=1)).timestamp()
    return start, end


def _context(raw_json):
    try:
        data = json.loads(raw_json or "")
    except ValueError:
        return None, None
    return data.get("cwd"), data.get("session_id")


def _reversibility_reason(note: str) -> str:
    base = note.split(" | ", 1)[0]
    marker = "; reversibility: "
    return base.split(marker, 1)[1] if marker in base else ""


def project_name(cwd) -> str:
    return Path(cwd).name if cwd else "(unknown project)"


def display_target(action_type: str, target: str, cwd) -> str:
    if not target:
        return ""
    if action_type == "file_write" and cwd and target.startswith(cwd.rstrip("/") + "/"):
        return target[len(cwd.rstrip("/")) + 1:]
    if action_type == "execute":
        return LEADING_CD.sub("", target, count=1)
    return target


def list_days(conn: sqlite3.Connection):
    rows = conn.execute(
        "SELECT date(timestamp, 'unixepoch', 'localtime') AS d, attribution, raw_json "
        "FROM actions ORDER BY d DESC"
    ).fetchall()
    days = {}
    for d, attribution, raw in rows:
        entry = days.setdefault(d, {"day": d, "total": 0, "agent": 0, "human": 0,
                                    "unknown": 0, "_projects": {}})
        entry[attribution] += 1
        entry["total"] += 1
        name = project_name(_context(raw)[0])
        entry["_projects"][name] = entry["_projects"].get(name, 0) + 1
    for entry in days.values():
        top = sorted(entry.pop("_projects").items(), key=lambda kv: -kv[1])
        entry["projects"] = [n for n, _ in top[:3]] + (["…"] if len(top) > 3 else [])
    return list(days.values())


def day_statement(conn: sqlite3.Connection, day: date, type_filter: str | None = None,
                  agent_filter: str | None = None, user_filter: str | None = None):
    start, end = day_bounds(day)
    rows = conn.execute(
        "SELECT id, timestamp, agent, action_type, target, amount, currency, artifact_link, "
        "reversible, attribution, confidence_note, raw_json, user "
        "FROM actions WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (start, end),
    ).fetchall()
    actions = []
    for r in rows:
        cwd, session_id = _context(r[11])
        target = r[4] or ""
        shown = display_target(r[3], target, cwd)
        actions.append({
            "id": r[0],
            "time": datetime.fromtimestamp(r[1]).strftime("%H:%M:%S"),
            "agent": r[2],
            "user": r[12] or "(unknown user)",
            "action_type": r[3],
            "target": target,
            "short_target": shown.splitlines()[0][:SHORT_TARGET] if shown else "",
            "full_target": shown,
            "is_long": len(shown) > SHORT_TARGET or "\n" in shown,
            "amount": r[5],
            "currency": r[6],
            "artifact_link": r[7],
            "reversible": {None: "not assessed", 0: "no", 1: "yes"}.get(r[8], "not assessed"),
            "reversible_reason": _reversibility_reason(r[10] or ""),
            "attribution": r[9],
            "note": r[10] or "",
            "project": project_name(cwd),
            "session_id": session_id,
        })

    by_type = {}
    by_attribution = {"agent": 0, "human": 0, "unknown": 0}
    by_project = {}
    by_agent = {}
    by_user = {}
    money = {}
    file_targets = {}
    uncovered = 0
    for a in actions:
        by_type[a["action_type"]] = by_type.get(a["action_type"], 0) + 1
        by_attribution[a["attribution"]] += 1
        by_project[a["project"]] = by_project.get(a["project"], 0) + 1
        by_agent[a["agent"]] = by_agent.get(a["agent"], 0) + 1
        by_user[a["user"]] = by_user.get(a["user"], 0) + 1
        if a["amount"] is not None:
            cur = a["currency"] or "?"
            money[cur] = money.get(cur, 0) + a["amount"]
        if a["action_type"] == "file_write" and a["target"]:
            file_targets[a["target"]] = file_targets.get(a["target"], 0) + 1
        if "monitor was not running" in a["note"]:
            uncovered += 1

    shown = [a for a in actions
             if (not type_filter or a["action_type"] == type_filter)
             and (not agent_filter or a["agent"] == agent_filter)
             and (not user_filter or a["user"] == user_filter)]
    groups = {}
    for a in shown:
        if a["attribution"] != "unknown":
            groups.setdefault(a["project"], []).append(a)
    project_groups = sorted(groups.items(), key=lambda kv: -len(kv[1]))

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
        "type_filter": type_filter,
        "agent_filter": agent_filter,
        "user_filter": user_filter,
        "by_agent": sorted(by_agent.items(), key=lambda kv: -kv[1]),
        "by_user": sorted(by_user.items(), key=lambda kv: -kv[1]),
        "project_groups": project_groups,
        "shown_count": len(shown),
        "unknown": [a for a in shown if a["attribution"] == "unknown"],
        "total": len(actions),
        "by_type": sorted(by_type.items(), key=lambda kv: -kv[1]),
        "by_attribution": by_attribution,
        "by_project": sorted(by_project.items(), key=lambda kv: -kv[1]),
        "money": money,
        "top_files": sorted(file_targets.items(), key=lambda kv: -kv[1])[:5],
        "uncovered": uncovered,
        "monitor_intervals": monitor_intervals,
        "monitor_minutes": round(covered_seconds / 60),
        "prev_day": (day - timedelta(days=1)).isoformat(),
        "next_day": (day + timedelta(days=1)).isoformat(),
    }

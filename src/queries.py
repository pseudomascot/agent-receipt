"""Read-side queries shared by the statement page and the daily summary."""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from describe import describe

# What the receipt can and cannot see. Shown on every page so gaps are never silent.
STATIC_COVERAGE = [
    ("Claude Code sessions (CLI and the desktop app's Code tab)", "covered",
     "Read from Claude Code's own transcripts. Every side-effect tool call is listed."),
    ("Cowork (Claude Desktop's local agent mode, including scheduled tasks)", "covered",
     "Read from Cowork's audit transcripts. Actions inside its sandbox and via Claude in Chrome are listed."),
    ("Codex (OpenAI's coding agent, app and CLI)", "covered",
     "Read from Codex's session rollouts: commands run, files added/changed/deleted, MCP tools, generated files."),
    ("Any other agent that writes receipt lines", "covered",
     "One JSON line per side effect appended to ~/.agent-receipt/inbox/ (see docs/RECEIPT_LINE.md)."),
    ("Google Antigravity", "not covered",
     "Keeps conversations as binary protobuf files with no published schema."),
    ("Physical keyboard/mouse activity", "partial",
     "Only while the input monitor is running; see the monitor intervals for each day."),
    ("Claude Desktop chat (not the Code tab)", "not covered",
     "Keeps no usable local log; its actions do not appear here."),
]


def coverage_notes(email_on: bool | None = None, stripe_on: bool | None = None,
                   calendar_on: bool | None = None, google_on: bool | None = None) -> list:
    if email_on is None or stripe_on is None or calendar_on is None or google_on is None:
        from config import calendar_configured, email_configured, google_calendar_configured, stripe_configured
        email_on = email_configured() if email_on is None else email_on
        stripe_on = stripe_configured() if stripe_on is None else stripe_on
        calendar_on = calendar_configured() if calendar_on is None else calendar_on
        google_on = google_calendar_configured() if google_on is None else google_on
    dynamic = [
        ("Calendar (the macOS Calendar app and everything it syncs)", "covered" if calendar_on else "not covered",
         "Events created, changed, or deleted, read locally by asking the Calendar app." if calendar_on
         else "Set RECEIPT_CALENDAR=on in .env (docs/CALENDAR.md)."),
        ("Google Calendar (the agent's Google account)", "covered" if google_on else "not covered",
         "Events with real creation times; events created by an agent account are attributed to it." if google_on
         else "Needs a Google OAuth client and a one-time sign-in (docs/GOOGLE_CALENDAR.md)."),
        ("Email sent from the agent's mailbox", "covered" if email_on else "not covered",
         "Polled read-only over IMAP from the mailbox in .env." if email_on
         else "Set RECEIPT_IMAP_USER / RECEIPT_IMAP_PASSWORD in .env (docs/EMAIL.md)."),
        ("Card charges (issuer alert emails)", "covered" if email_on else "not covered",
         "Recognised alert emails in the agent mailbox become purchases." if email_on
         else "Same mailbox setup; alerts must be routed to it."),
        ("Card charges (Stripe Issuing) and charges collected via Stripe", "covered" if stripe_on else "not covered",
         "Polled from the Stripe API with the key in .env, at Stripe's own event times." if stripe_on
         else "Set RECEIPT_STRIPE_TEST_KEY (or RECEIPT_STRIPE_KEY) in .env (docs/STRIPE.md)."),
        ("Crypto wallet", "not covered", "Planned."),
    ]
    return STATIC_COVERAGE + dynamic


def not_covered(email_on: bool | None = None, stripe_on: bool | None = None,
                calendar_on: bool | None = None, google_on: bool | None = None) -> list:
    return [name for name, status, _ in coverage_notes(email_on, stripe_on, calendar_on, google_on)
            if status == "not covered"]
ACTION_TYPES = ("send_email", "create_event", "purchase", "file_write", "post", "execute", "other")
TYPE_LABELS = {
    "file_write": "File edits", "execute": "Commands run", "send_email": "Emails sent",
    "create_event": "Calendar events", "purchase": "Purchases", "post": "Messages & posts",
    "other": "Browser & other",
}


TYPE_WORDS = {
    "file_write": ("file edit", "file edits"), "execute": ("command run", "commands run"),
    "send_email": ("email sent", "emails sent"), "create_event": ("calendar event", "calendar events"),
    "purchase": ("purchase", "purchases"), "post": ("message or post", "messages & posts"),
    "other": ("browser/other action", "browser & other actions"),
}


def type_label(action_type: str) -> str:
    return TYPE_LABELS.get(action_type, action_type)


def type_words(action_type: str, n: int) -> str:
    one, many = TYPE_WORDS.get(action_type, (action_type, action_type))
    return one if n == 1 else many

# A leading `cd "<dir>" && ` says where, which the Project column already shows.
LEADING_CD = re.compile(r'^cd\s+("[^"]*"|\'[^\']*\'|\S+)\s*&&\s*')
SHORT_TARGET = 110


MERGE_GAP_SECONDS = 120


def merge_intervals(intervals, gap: float = MERGE_GAP_SECONDS):
    """Join (start, end) pairs that overlap or sit within `gap` seconds of each other."""
    merged = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def duration_text(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    hours, rem = divmod(minutes, 60)
    return f"{hours} hour{'' if hours == 1 else 's'}" + (f" {rem} min" if rem else "")


def day_bounds(day: date):
    start = datetime(day.year, day.month, day.day).timestamp()
    end = (datetime(day.year, day.month, day.day) + timedelta(days=1)).timestamp()
    return start, end


def _context(raw_json):
    """(cwd, session_id, project label or None) from a row's raw_json."""
    try:
        data = json.loads(raw_json or "")
    except ValueError:
        return None, None, None
    return data.get("cwd"), data.get("session_id"), data.get("project")


def _tool(raw_json) -> str:
    try:
        return str(json.loads(raw_json or "{}").get("tool") or "")
    except ValueError:
        return ""


def _reversibility_reason(note: str) -> str:
    base = note.split(" | ", 1)[0]
    marker = "; reversibility: "
    return base.split(marker, 1)[1] if marker in base else ""


def project_name(cwd, label=None) -> str:
    if label:
        return label
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
        cwd, _, label = _context(raw)
        name = project_name(cwd, label)
        entry["_projects"][name] = entry["_projects"].get(name, 0) + 1
    for entry in days.values():
        top = sorted(entry.pop("_projects").items(), key=lambda kv: -kv[1])
        entry["projects"] = [n for n, _ in top[:3]] + (["…"] if len(top) > 3 else [])
    return list(days.values())


def earliest_day(conn: sqlite3.Connection) -> date | None:
    row = conn.execute("SELECT MIN(timestamp) FROM actions").fetchone()
    return datetime.fromtimestamp(row[0]).date() if row and row[0] else None


def day_statement(conn: sqlite3.Connection, day: date, type_filter: str | None = None,
                  agent_filter: str | None = None, user_filter: str | None = None):
    return statement(conn, day, day, type_filter, agent_filter, user_filter)


def statement(conn: sqlite3.Connection, start_day: date, end_day: date,
              type_filter: str | None = None, agent_filter: str | None = None,
              user_filter: str | None = None):
    """Everything the statement page and summary need for start_day..end_day inclusive."""
    if end_day < start_day:
        start_day, end_day = end_day, start_day
    start = day_bounds(start_day)[0]
    end = day_bounds(end_day)[1]
    single_day = start_day == end_day
    time_format = "%H:%M:%S" if single_day else "%Y-%m-%d %H:%M"
    rows = conn.execute(
        "SELECT id, timestamp, agent, action_type, target, amount, currency, artifact_link, "
        "reversible, attribution, confidence_note, raw_json, user, source_ref "
        "FROM actions WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (start, end),
    ).fetchall()
    actions = []
    for r in rows:
        cwd, session_id, label = _context(r[11])
        target = r[4] or ""
        shown = display_target(r[3], target, cwd)
        actions.append({
            "id": r[0],
            "timestamp": r[1],
            "time": datetime.fromtimestamp(r[1]).strftime(time_format),
            "day": datetime.fromtimestamp(r[1]).date().isoformat(),
            "agent": r[2],
            "user": r[12] or "(unknown user)",
            "action_type": r[3],
            "target": target,
            "what": describe(r[3], target, _tool(r[11]), cwd, {"amount": r[5], "currency": r[6]}),
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
            "project": project_name(cwd, label),
            "session_id": session_id,
            "source_ref": r[13],
        })

    by_type = {}
    by_attribution = {"agent": 0, "human": 0, "unknown": 0}
    by_project = {}
    by_agent = {}
    by_user = {}
    by_day = {}
    money = {}
    file_targets = {}
    uncovered = 0
    for a in actions:
        by_type[a["action_type"]] = by_type.get(a["action_type"], 0) + 1
        by_attribution[a["attribution"]] += 1
        by_project[a["project"]] = by_project.get(a["project"], 0) + 1
        by_agent[a["agent"]] = by_agent.get(a["agent"], 0) + 1
        by_user[a["user"]] = by_user.get(a["user"], 0) + 1
        by_day[a["day"]] = by_day.get(a["day"], 0) + 1
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
    # Newest first, like a bank statement: rows within a project, and projects by
    # their latest action. (Exports stay chronological.)
    groups = {}
    for a in sorted(shown, key=lambda a: -a["timestamp"]):
        if a["attribution"] != "unknown":
            groups.setdefault(a["project"], []).append(a)
    project_groups = sorted(groups.items(), key=lambda kv: -kv[1][0]["timestamp"])

    intervals = conn.execute(
        "SELECT started_at, ended_at FROM coverage WHERE source = 'input' "
        "AND ended_at >= ? AND started_at < ? ORDER BY started_at",
        (start, end),
    ).fetchall()
    clipped = [(max(s, start), min(e, end)) for s, e in intervals]
    covered_seconds = sum(e - s for s, e in merge_intervals(clipped, 0))
    monitor_intervals = [(datetime.fromtimestamp(s).strftime("%H:%M"), datetime.fromtimestamp(e).strftime("%H:%M"))
                         for s, e in merge_intervals(clipped)]

    days_in_range = (end_day - start_day).days + 1
    return {
        "day": start_day.isoformat(),
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "single_day": single_day,
        "days_in_range": days_in_range,
        "label": start_day.isoformat() if single_day else f"{start_day.isoformat()} to {end_day.isoformat()}",
        "type_filter": type_filter,
        "agent_filter": agent_filter,
        "user_filter": user_filter,
        "by_agent": sorted(by_agent.items(), key=lambda kv: -kv[1]),
        "by_user": sorted(by_user.items(), key=lambda kv: -kv[1]),
        "by_day": sorted(by_day.items(), reverse=True),
        "shown": shown,
        "project_groups": project_groups,
        "shown_count": len(shown),
        "unknown": sorted([a for a in shown if a["attribution"] == "unknown"], key=lambda a: -a["timestamp"]),
        "total": len(actions),
        "by_type": sorted(by_type.items(), key=lambda kv: -kv[1]),
        "by_attribution": by_attribution,
        "by_project": sorted(by_project.items(), key=lambda kv: -kv[1]),
        "money": money,
        "top_files": sorted(file_targets.items(), key=lambda kv: -kv[1])[:5],
        "uncovered": uncovered,
        "monitor_intervals": monitor_intervals,
        "monitor_minutes": round(covered_seconds / 60),
        "monitor_text": duration_text(covered_seconds),
        "prev_day": (start_day - timedelta(days=1)).isoformat(),
        "next_day": (start_day + timedelta(days=1)).isoformat(),
    }

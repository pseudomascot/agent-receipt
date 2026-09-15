"""Alert rules: which receipt lines deserve a look right away.

Rules are plain functions over one action row. Matches are stored in `alerts`
(one per action and rule) and, when fresh, raised as a macOS notification.
Everything stays local.
"""

import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

from describe import describe

BURST_WINDOW_SECONDS = 3600
BURST_THRESHOLD = 5
MONEY_THRESHOLD = 100.0
FRESH_SECONDS = 24 * 3600      # only notify for actions this recent; older ones just wait in Needs review

RULES = {
    "unattended_irreversible": "an irreversible action with nobody at the keyboard",
    "irreversible_burst": f"{BURST_THRESHOLD}+ irreversible actions by one agent within an hour",
    "money_over_threshold": f"a payment over {MONEY_THRESHOLD:.0f}",
    "unknown_attribution": "an action nobody can be confirmed for",
    "retired_agent_acted": "an action by an agent after it was retired on the Agents page",
}


def _unattended(row: dict) -> bool:
    if "(scheduled task)" in (row["agent"] or ""):
        return True
    return "no physical input in prior" in (row["confidence_note"] or "")


def evaluate(conn: sqlite3.Connection) -> list[dict]:
    """Check actions not yet evaluated; store and return new alerts."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'alerts_evaluated_to'").fetchone()
    last_id = int(row[0]) if row else 0
    rows = conn.execute(
        "SELECT id, timestamp, agent, action_type, target, amount, currency, reversible, "
        "attribution, confidence_note, raw_json FROM actions WHERE id > ? ORDER BY id",
        (last_id,),
    ).fetchall()
    cols = ["id", "timestamp", "agent", "action_type", "target", "amount", "currency",
            "reversible", "attribution", "confidence_note", "raw_json"]
    new = []
    max_id = last_id
    for r in rows:
        a = dict(zip(cols, r))
        max_id = max(max_id, a["id"])
        for rule, message in _matches(conn, a):
            cur = conn.execute(
                "INSERT OR IGNORE INTO alerts (action_id, rule, message, created_at) VALUES (?, ?, ?, ?)",
                (a["id"], rule, message, time.time()),
            )
            if cur.rowcount:
                new.append({"action_id": a["id"], "rule": rule, "message": message,
                            "timestamp": a["timestamp"], "agent": a["agent"], "target": a["target"]})
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('alerts_evaluated_to', ?)", (str(max_id),))
    conn.commit()
    return new


def _what(a: dict) -> str:
    try:
        raw = json.loads(a.get("raw_json") or "{}")
    except ValueError:
        raw = {}
    return describe(a["action_type"], a["target"], str(raw.get("tool") or ""), raw.get("cwd"),
                    {"amount": a.get("amount"), "currency": a.get("currency")})


def _matches(conn: sqlite3.Connection, a: dict):
    if a["reversible"] == 0 and _unattended(a):
        yield "unattended_irreversible", f"{a['agent']}, unattended: {_short(_what(a))}"
    if a["reversible"] == 0:
        n = conn.execute(
            "SELECT COUNT(*) FROM actions WHERE agent = ? AND reversible = 0 "
            "AND timestamp > ? AND timestamp <= ?",
            (a["agent"], a["timestamp"] - BURST_WINDOW_SECONDS, a["timestamp"]),
        ).fetchone()[0]
        if n >= BURST_THRESHOLD:
            yield "irreversible_burst", f"{a['agent']}: {n} irreversible actions in the last hour"
    if a["amount"] is not None and a["amount"] > MONEY_THRESHOLD:
        yield "money_over_threshold", f"{a['agent']}: {_short(_what(a))}"
    if a["attribution"] == "unknown":
        yield "unknown_attribution", f"nobody can be confirmed for: {_short(_what(a))}"
    status = conn.execute("SELECT retired_at FROM agent_status WHERE agent = ?", (a["agent"],)).fetchone()
    if status and a["timestamp"] > status[0]:
        yield "retired_agent_acted", f"{a['agent']} acted after being retired: {_short(_what(a))}"


def _short(text, n=80) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


def unseen(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT al.id, al.rule, al.message, al.created_at, ac.id, ac.timestamp, ac.agent, "
        "ac.action_type, ac.target, ac.confidence_note, ac.raw_json, ac.amount, ac.currency "
        "FROM alerts al JOIN actions ac ON ac.id = al.action_id "
        "WHERE al.seen_at IS NULL ORDER BY ac.timestamp DESC"
    ).fetchall()
    out = []
    for r in rows:
        try:
            raw = json.loads(r[10] or "{}")
        except ValueError:
            raw = {}
        project = raw.get("project") or (raw.get("cwd") or "").rsplit("/", 1)[-1]
        out.append({
            "id": r[0], "rule": r[1], "rule_text": RULES.get(r[1], r[1]), "message": r[2],
            "action_id": r[4], "time": datetime.fromtimestamp(r[5]).strftime("%Y-%m-%d %H:%M"),
            "day": datetime.fromtimestamp(r[5]).date().isoformat(), "agent": r[6],
            "action_type": r[7], "target": r[8] or "", "project": project or "(unknown project)",
            "what": describe(r[7], r[8], str(raw.get("tool") or ""), raw.get("cwd"),
                             {"amount": r[11], "currency": r[12]}),
            "reason": (r[9] or "").split("; reversibility: ")[-1].split(" | ")[0],
        })
    return out


def unseen_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM alerts al JOIN actions ac ON ac.id = al.action_id WHERE al.seen_at IS NULL"
    ).fetchone()[0]


def unseen_action_ids(conn: sqlite3.Connection, start_ts: float, end_ts: float) -> dict:
    rows = conn.execute(
        "SELECT al.action_id, al.rule FROM alerts al JOIN actions ac ON ac.id = al.action_id "
        "WHERE al.seen_at IS NULL AND ac.timestamp >= ? AND ac.timestamp < ?", (start_ts, end_ts),
    ).fetchall()
    out = {}
    for action_id, rule in rows:
        out.setdefault(action_id, []).append(rule)
    return out


def mark_seen(conn: sqlite3.Connection, alert_ids=None) -> int:
    now = time.time()
    if alert_ids is None:
        cur = conn.execute("UPDATE alerts SET seen_at = ? WHERE seen_at IS NULL", (now,))
    else:
        cur = conn.executemany("UPDATE alerts SET seen_at = ? WHERE id = ? AND seen_at IS NULL",
                               [(now, i) for i in alert_ids])
    conn.commit()
    return cur.rowcount if alert_ids is None else len(alert_ids)


def notify(title: str, message: str) -> None:
    """macOS notification; silently does nothing elsewhere or if osascript fails."""
    if sys.platform != "darwin":
        return
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def notify_new(new: list[dict], send=notify) -> int:
    """Notify about fresh alerts (recent actions). Returns how many notifications were sent."""
    fresh = [n for n in new if time.time() - n["timestamp"] <= FRESH_SECONDS]
    if not fresh:
        return 0
    if len(fresh) <= 3:
        for n in fresh:
            send("Agent Receipt — needs review", n["message"])
        return len(fresh)
    send("Agent Receipt — needs review", f"{len(fresh)} new alerts; open the Needs review page")
    return 1

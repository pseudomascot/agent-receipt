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
    "irreversible_burst": "several irreversible actions by one agent within an hour",
    "money_over_threshold": "a payment over the threshold set in Alert rules",
    "unknown_attribution": "an action nobody can be confirmed for",
    "retired_agent_acted": "an action by an agent after it was retired on the Agents page",
}

# Adjustable from the Needs review page; stored in `meta` under "alert.<key>". Changing them
# is recorded on the receipt and affects actions evaluated from then on (old alerts stay).
DEFAULTS = {
    "money_threshold": MONEY_THRESHOLD,   # amount, in the row's own currency
    "money_scope": "agent",               # "agent": not a person's own purchase (agent or unconfirmed); "all": anyone's
    "burst_threshold": BURST_THRESHOLD,   # irreversible actions per hour by one agent
    **{f"on.{rule}": True for rule in RULES},
}
SCOPES = ("agent", "all")


def settings(conn: sqlite3.Connection) -> dict:
    """Current rule settings: defaults overridden by whatever is stored in meta."""
    cfg = dict(DEFAULTS)
    for key, value in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'alert.%'"):
        name = key[len("alert."):]
        if name not in cfg:
            continue
        try:
            if name == "money_threshold":
                cfg[name] = float(value)
            elif name == "burst_threshold":
                cfg[name] = int(value)
            elif name == "money_scope":
                cfg[name] = value if value in SCOPES else cfg[name]
            else:
                cfg[name] = value == "1"
        except ValueError:
            pass
    return cfg


def describe_settings(cfg: dict) -> str:
    on = [r for r in RULES if cfg.get(f"on.{r}")]
    off = [r.replace("_", " ") for r in RULES if not cfg.get(f"on.{r}")]
    parts = [f"payment over {cfg['money_threshold']:,.2f} ({'not a person' if cfg['money_scope'] == 'agent' else 'anyone'})",
             f"{cfg['burst_threshold']}+ irreversible actions in an hour"]
    if off:
        parts.append("off: " + ", ".join(off))
    return "; ".join(parts) + f"; {len(on)} of {len(RULES)} rules on"


def save_settings(conn: sqlite3.Connection, form: dict, user: str) -> dict:
    """Apply a settings form ({key: value-or-list}); records the change on the receipt if anything moved."""
    from agents import INSERT, RECEIPT_AGENT
    before = settings(conn)
    new = dict(before)

    def last(key):
        v = form.get(key)
        if isinstance(v, (list, tuple)):
            return v[-1] if v else None
        return v
    if last("money_threshold") not in (None, ""):
        try:
            new["money_threshold"] = max(0.0, float(str(last("money_threshold")).replace(",", "")))
        except ValueError:
            pass
    if last("burst_threshold") not in (None, ""):
        try:
            new["burst_threshold"] = max(2, int(float(last("burst_threshold"))))
        except ValueError:
            pass
    if last("money_scope") in SCOPES:
        new["money_scope"] = last("money_scope")
    for rule in RULES:
        v = last(f"on.{rule}")
        if v is not None:
            new[f"on.{rule}"] = str(v).lower() in ("1", "true", "on", "yes")
    if new == before:
        return before
    for key, value in new.items():
        stored = ("1" if value else "0") if isinstance(value, bool) else str(value)
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (f"alert.{key}", stored))
    now = time.time()
    conn.execute(INSERT, (
        now, RECEIPT_AGENT, user, "input", "other", f"Changed alert rules: {describe_settings(new)}", 1, "human",
        "the Alert rules form was saved in Agent Receipt; reversibility: can be changed again",
        json.dumps({"source": "agent-receipt", "tool": "agent-receipt:rules", "before": before, "after": new}),
        f"agent-receipt:rules:{now:.3f}",
    ))
    conn.commit()
    return new


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
    cfg = settings(conn)
    new = []
    max_id = last_id
    for r in rows:
        a = dict(zip(cols, r))
        max_id = max(max_id, a["id"])
        for rule, message in _matches(conn, a, cfg):
            if not cfg.get(f"on.{rule}", True):
                continue
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


def _matches(conn: sqlite3.Connection, a: dict, cfg: dict | None = None):
    cfg = cfg or DEFAULTS
    if a["reversible"] == 0 and _unattended(a):
        yield "unattended_irreversible", f"{a['agent']}, unattended: {_short(_what(a))}"
    if a["reversible"] == 0:
        n = conn.execute(
            "SELECT COUNT(*) FROM actions WHERE agent = ? AND reversible = 0 "
            "AND timestamp > ? AND timestamp <= ?",
            (a["agent"], a["timestamp"] - BURST_WINDOW_SECONDS, a["timestamp"]),
        ).fetchone()[0]
        if n >= cfg["burst_threshold"]:
            yield "irreversible_burst", f"{a['agent']}: {n} irreversible actions in the last hour"
    # Scope "agent" = anything that is not a person's own purchase: the agent's, or one
    # nobody can be confirmed for. A named person's card purchase is their business.
    if (a["amount"] is not None and a["amount"] > cfg["money_threshold"]
            and (cfg["money_scope"] == "all" or a["attribution"] != "human")):
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

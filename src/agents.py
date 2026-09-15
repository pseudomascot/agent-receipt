"""Agent identities: everyone who has acted on this Mac, and which of them are retired.

Retiring an agent never touches its history. It records that, from this
moment, the agent is not supposed to act; anything it does afterwards trips
the `retired_agent_acted` alert. The retire or restore itself is written to
the receipt as a row, so the decision is on the record too.

Identity here is observed, not issued: a label comes from the agent's own log
(Claude Code, Cowork, Codex), from a credential the receipt reads (a mailbox,
a Stripe account, a Google account), or from what an agent declares through
the receipt-line inbox.
"""

import json
import sqlite3
import time
from datetime import datetime

from config import calendar_settings, email_settings, google_calendar_settings, load_env, stripe_settings

RECEIPT_AGENT = "agent receipt"          # rows the app writes about its own buttons
LOCAL_HARNESSES = {"claude-code": "Claude Code", "cowork": "Cowork", "codex": "Codex"}

INSERT = """
INSERT INTO actions
    (timestamp, agent, user, source, action_type, target, reversible, attribution,
     confidence_note, raw_json, source_ref)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def known_identities(env: dict | None = None) -> dict:
    """Agent label -> (kind, what stands behind it), from the configured connectors."""
    env = env if env is not None else load_env()
    out = {}
    mail = email_settings(env)
    if mail:
        out[mail["agent"]] = ("mailbox", f"its own mailbox, {mail['user']}")
    stripe = stripe_settings(env)
    if stripe:
        out[stripe["agent"]] = ("card", f"a Stripe account ({stripe['mode']} mode)")
    cal = calendar_settings(env)
    if cal:
        out[cal["agent"]] = ("calendar", "the Mac Calendar app (no login of its own)")
    google = google_calendar_settings(env)
    if google:
        out[google["agent"]] = ("calendar", "Google Calendar, read through the receipt's own token")
        for email in google["agent_emails"]:
            out[f"google calendar ({email})"] = ("google account", f"its own Google account, {email}")
    return out


def kind_of(name: str, known: dict) -> tuple[str, str]:
    if name in known:
        return known[name]
    base = name.split(" (", 1)[0].split(" / ", 1)[0]
    if base in LOCAL_HARNESSES:
        if " / " in name:
            return "sub-agent", f"a worker started by {LOCAL_HARNESSES[base]}, read from its own transcript"
        what = f"{LOCAL_HARNESSES[base]} on this Mac, read from its own log"
        if "(scheduled task)" in name:
            what += "; runs on a schedule with nobody present"
        return "local agent", what
    if name.startswith("google calendar ("):
        return "google account", "a Google account not listed in RECEIPT_GOOGLE_AGENT_EMAILS"
    return "declared", "declares its own actions through the receipt-line inbox"


ENTRYPOINTS = {"claude-desktop": "in the Claude app", "cli": "in the Terminal"}


def nicknames(conn: sqlite3.Connection) -> dict:
    return dict(conn.execute("SELECT agent, nickname FROM agent_names").fetchall())


def set_nickname(conn: sqlite3.Connection, agent: str, nickname: str, user: str) -> int | None:
    """Name an agent (empty nickname clears it). Recorded on the receipt like every other button."""
    nickname = " ".join((nickname or "").split())[:60]
    if not agent:
        return None
    current = nicknames(conn).get(agent, "")
    if nickname == current:
        return None
    now = time.time()
    if nickname:
        conn.execute("INSERT OR REPLACE INTO agent_names (agent, nickname, set_at, by_user) VALUES (?, ?, ?, ?)",
                     (agent, nickname, now, user))
    else:
        conn.execute("DELETE FROM agent_names WHERE agent = ?", (agent,))
    return _record(conn, "name", agent, user, nickname, now)


def display(name: str, nicks: dict | None = None, known: dict | None = None) -> dict:
    """Plain-English identity for a raw agent label.

    {"name": what to print, "sub": one line under it, "kind": pill text, "raw": the label, "nickname": ""|str}
    A nickname the person chose wins; the generated plain name then becomes the subtitle."""
    nicks = nicks or {}
    known = known if known is not None else known_identities()
    kind, what = kind_of(name, known)
    pretty, sub = _plain(name, kind, what, known)
    nick = nicks.get(name)
    if nick:
        return {"name": nick, "sub": pretty if pretty != name else what, "kind": kind, "raw": name, "nickname": nick}
    return {"name": pretty, "sub": sub, "kind": kind, "raw": name, "nickname": ""}


def _plain(name: str, kind: str, what: str, known: dict) -> tuple[str, str]:
    if name == RECEIPT_AGENT:
        return "Agent Receipt", "a button you pressed in the app"
    base, _, rest = name.partition(" / ")
    head, _, paren = base.partition(" (")
    paren = paren[:-1] if paren.endswith(")") else paren
    if head == "google calendar" and paren:
        return "Google account", paren
    if name in known:
        return name, what
    if head == "claude-code":
        where = ENTRYPOINTS.get(paren, paren)
        if rest:
            role, _, task = rest.partition(": ")
            return "Sub-agent of Claude Code", (task or role) + (f" · {where}" if where else "")
        return "Claude Code", where
    if head == "cowork":
        if "scheduled" in paren:
            return "Cowork · scheduled task", "runs on a schedule with nobody present"
        return "Cowork", "in the Claude app"
    if head == "codex":
        return "Codex (OpenAI)", paren
    return name, what


def _fmt(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def retired(conn: sqlite3.Connection) -> dict:
    """agent -> {retired_at, when, note, by_user} for every retired agent."""
    return {r[0]: {"retired_at": r[1], "when": _fmt(r[1]), "note": r[2] or "", "by_user": r[3] or ""}
            for r in conn.execute("SELECT agent, retired_at, note, by_user FROM agent_status")}


def list_agents(conn: sqlite3.Connection, env: dict | None = None) -> list[dict]:
    """One entry per identity: observed agents, configured-but-quiet ones, and retired ones."""
    known = known_identities(env)
    status = retired(conn)
    nicks = nicknames(conn)
    stats = {}
    for name, total, first, last, users, irreversible, money in conn.execute(
        "SELECT agent, COUNT(*), MIN(timestamp), MAX(timestamp), GROUP_CONCAT(DISTINCT user), "
        "SUM(reversible = 0), SUM(amount IS NOT NULL) FROM actions WHERE agent != ? GROUP BY agent",
        (RECEIPT_AGENT,),
    ):
        stats[name] = {"total": total, "first": first, "last": last, "users": users or "",
                       "irreversible": irreversible or 0, "money": money or 0}
    open_alerts = dict(conn.execute(
        "SELECT ac.agent, COUNT(*) FROM alerts al JOIN actions ac ON ac.id = al.action_id "
        "WHERE al.seen_at IS NULL GROUP BY ac.agent"
    ).fetchall())
    by_type = {}
    for name, action_type, n in conn.execute(
        "SELECT agent, action_type, COUNT(*) FROM actions GROUP BY agent, action_type"
    ):
        by_type.setdefault(name, {})[action_type] = n

    out = []
    for name in set(stats) | set(known) | set(status):
        s = stats.get(name)
        kind, what = kind_of(name, known)
        d = display(name, nicks, known)
        out.append({
            "name": name, "kind": kind, "what": what, "label": d["name"], "sub": d["sub"], "nickname": d["nickname"],
            "users": sorted(u for u in (s["users"].split(",") if s else []) if u),
            "first_seen": _fmt(s["first"]) if s else "", "last_seen": _fmt(s["last"]) if s else "",
            "last_ts": s["last"] if s else 0,
            "total": s["total"] if s else 0, "irreversible": s["irreversible"] if s else 0,
            "money": s["money"] if s else 0, "by_type": by_type.get(name, {}),
            "open_alerts": open_alerts.get(name, 0), "retired": status.get(name),
        })
    out.sort(key=lambda a: (a["retired"] is not None, -a["last_ts"], a["name"]))
    return out


def retire(conn: sqlite3.Connection, agent: str, user: str, note: str = "") -> int | None:
    """Mark an agent retired. Returns the id of the receipt row recording it, or None if already retired."""
    if not agent or agent in retired(conn):
        return None
    now = time.time()
    conn.execute("INSERT INTO agent_status (agent, retired_at, note, by_user) VALUES (?, ?, ?, ?)",
                 (agent, now, note, user))
    return _record(conn, "retire", agent, user, note, now)


def restore(conn: sqlite3.Connection, agent: str, user: str, note: str = "") -> int | None:
    """Undo a retirement. Returns the id of the receipt row recording it, or None if not retired."""
    if agent not in retired(conn):
        return None
    now = time.time()
    conn.execute("DELETE FROM agent_status WHERE agent = ?", (agent,))
    return _record(conn, "restore", agent, user, note, now)


def _record(conn, event: str, agent: str, user: str, note: str, now: float) -> int:
    verb = {"retire": "Retired", "restore": "Restored", "name": "Named"}[event]
    if event == "name":
        target = f"Named agent: {agent} → “{note}”" if note else f"Cleared the name of agent: {agent}"
        reason = "can be renamed again"
    else:
        target = f"{verb} agent: {agent}" + (f" — {note}" if note else "")
        reason = "can be restored from the Agents page" if event == "retire" else "can be retired again"
    cur = conn.execute(INSERT, (
        now, RECEIPT_AGENT, user, "input", "other", target, 1, "human",
        f"the {event} button was pressed in Agent Receipt; reversibility: {reason}",
        json.dumps({"source": "agent-receipt", "tool": f"agent-receipt:{event}", "agent": agent, "note": note}),
        f"agent-receipt:{event}:{agent}:{now:.3f}",
    ))
    conn.commit()
    return cur.lastrowid


def history(conn: sqlite3.Connection) -> list[dict]:
    """Every retire/restore the app has recorded, newest first."""
    out = []
    for action_id, ts, user, target, raw in conn.execute(
        "SELECT id, timestamp, user, target, raw_json FROM actions WHERE agent = ? ORDER BY timestamp DESC",
        (RECEIPT_AGENT,),
    ):
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            data = {}
        out.append({"id": action_id, "when": _fmt(ts), "day": datetime.fromtimestamp(ts).date().isoformat(),
                    "user": user or "", "what": target, "event": str(data.get("tool", "")).split(":")[-1],
                    "agent": data.get("agent", "")})
    return out

"""Build a demo receipt for screenshots and the commercial, and serve it on its own port.

    .venv/bin/python examples/demo_data.py          # builds demo_receipt.db and serves http://127.0.0.1:8767/

Everything in it is invented: a small plumbing business, "Harbor Plumbing", with a
bookkeeping agent that has its own mailbox and Ramp card, a coding agent working on
the website, a scheduled Cowork task that ran at 3 a.m., a sub-agent, a Cursor edit,
one purchase by a person, one unexplained email, and the alerts those produce.
It never touches agent_receipt.db (the real statement). The Settings page on the
demo server still reflects this Mac's real connector setup; every other page is demo.
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents import set_nickname  # noqa: E402
from alerts import evaluate  # noqa: E402
from correlator import correlate  # noqa: E402
from ramp_connector import _record as ramp_record  # noqa: E402
from store import connect  # noqa: E402

DB = ROOT / "demo_receipt.db"
PORT = 8767
SITE = "/Users/you/Projects/harbor-website"
BOOKS = "/Users/you/Projects/harbor-books"
MAILBOX = "mailbox books@harborplumbing.com"
CARD_AGENT = "ramp card (Harbor Plumbing)"


def at(hh: int, mm: int, days_ago: int = 0) -> float:
    d = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0) - timedelta(days=days_ago)
    return d.timestamp()


def raw(tool, cwd=None, **extra):
    return json.dumps({"tool": tool, "input": {}, "cwd": cwd, "session_id": "demo", "source": "claude-code", **extra})


def add(conn, ts, agent, source, action_type, target, *, user="you", amount=None, currency=None, link=None,
        reversible=None, attribution="agent", note="agent log", raw_json="{}", ref=None):
    conn.execute(
        "INSERT INTO actions (timestamp, agent, user, source, action_type, target, amount, currency, artifact_link, "
        "reversible, attribution, confidence_note, raw_json, source_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ts, agent, user, source, action_type, target, amount, currency, link, reversible, attribution, note, raw_json,
         ref or f"demo:{agent}:{ts}:{target[:30]}"))


def build(path: Path = DB) -> None:
    if path.exists():
        path.unlink()
    conn = connect(path)
    CC = "claude-code (claude-desktop)"
    CCA = "declared in Claude Code transcript, session demo; reversibility: "
    # --- the website project, this morning: Claude Code + a sub-agent ---
    add(conn, at(9, 4), CC, "log", "file_write", f"{SITE}/src/pages/booking.html", reversible=1,
        note=CCA + "the file can be edited back", raw_json=raw("Edit", SITE))
    add(conn, at(9, 6), CC, "log", "file_write", f"{SITE}/src/styles/booking.css", reversible=1,
        note=CCA + "the file can be edited back", raw_json=raw("Edit", SITE))
    add(conn, at(9, 9), CC, "log", "execute", "npm run build", reversible=1,
        note=CCA + "a build can be re-run", raw_json=raw("Bash", SITE))
    add(conn, at(9, 12), CC, "log", "other", "general-purpose: Fix the invoice PDF layout", reversible=1,
        note=CCA + "a sub-agent can be stopped", raw_json=raw("Agent", SITE))
    SUB = f"{CC} / general-purpose: Fix the invoice PDF layout"
    add(conn, at(9, 14), SUB, "log", "file_write", f"{BOOKS}/templates/invoice.html", reversible=1,
        note=CCA + "the file can be edited back", raw_json=raw("Edit", BOOKS, sidechain=True))
    add(conn, at(9, 15), SUB, "log", "file_write", f"{BOOKS}/templates/invoice.css", reversible=1,
        note=CCA + "the file can be edited back", raw_json=raw("Write", BOOKS, sidechain=True))
    add(conn, at(9, 31), CC, "log", "execute", "git commit -m 'Booking page: fix mobile layout'", reversible=1,
        note=CCA + "git keeps history", raw_json=raw("Bash", SITE))
    add(conn, at(9, 32), CC, "log", "execute", "git push origin main", reversible=0,
        note=CCA + "published to a remote, visible to others", raw_json=raw("Bash", SITE))
    # --- Cursor, one edit ---
    add(conn, at(10, 41), "cursor (grok-4.6)", "log", "file_write", f"{SITE}/README.md", reversible=1,
        note="declared in Cursor transcript (time from the turn's stamp, minute precision), session demo; reversibility: the file can be edited back",
        raw_json=json.dumps({"tool": "Write", "input": {}, "cwd": SITE, "session_id": "demo", "source": "cursor"}))
    # --- the scheduled task at 3 a.m.: Cowork ---
    COW = "cowork (scheduled task)"
    COWN = "declared in cowork transcript, session demo; reversibility: "
    add(conn, at(3, 12), COW, "log", "file_write", "/sessions/daily-report/reports/2026-09-16.md", reversible=1,
        note=COWN + "the file can be edited back", raw_json=json.dumps({"tool": "Write", "input": {}, "cwd": "/sessions/daily-report", "session_id": "demo", "source": "cowork", "project": "Daily job report"}))
    add(conn, at(3, 13), COW, "log", "execute", "rm -rf /sessions/daily-report/tmp", reversible=0,
        note=COWN + "deleted files are gone", raw_json=json.dumps({"tool": "Bash", "input": {}, "cwd": "/sessions/daily-report", "session_id": "demo", "source": "cowork", "project": "Daily job report"}))
    add(conn, at(3, 14), COW, "log", "post", "Slack #office: Daily job report for Tuesday", reversible=0,
        note=COWN + "a message, once sent, is out", raw_json=json.dumps({"tool": "mcp__slack__send_message", "input": {}, "cwd": "/sessions/daily-report", "session_id": "demo", "source": "cowork", "project": "Daily job report"}))
    # --- the bookkeeping agent: its own mailbox, calendar and Ramp card ---
    for hh, mm, who in ((8, 2, "m.reyes@gmail.com"), (8, 5, "office@bluewaterhomes.com"), (8, 7, "dan@oakridgeproperties.com")):
        add(conn, at(hh, mm), MAILBOX, "email", "send_email", who, reversible=0,
            note="by credential: sent from the agent's own mailbox books@harborplumbing.com, subject 'Invoice from Harbor Plumbing'; reversibility: a sent email cannot be recalled",
            raw_json=json.dumps({"source": "email", "subject": "Invoice from Harbor Plumbing"}), ref=f"demo:mail:{who}")
    add(conn, at(8, 9), MAILBOX, "email", "send_email", "unknown-recipient@example.net", reversible=0,
        note="sent from the agent's own mailbox, subject 'Re: quote'; reversibility: a sent email cannot be recalled",
        raw_json=json.dumps({"source": "email", "subject": "Re: quote"}), ref="demo:mail:unknown")
    add(conn, at(8, 20), "google calendar (books@harborplumbing.com)", "log", "create_event",
        "Site visit — 14 Elm St (leak under sink)", reversible=1, link="https://calendar.google.com/",
        note="by credential: created by the agent's Google account books@harborplumbing.com in Google Calendar 'primary'; reversibility: the event can be deleted",
        raw_json=json.dumps({"source": "google_calendar", "tool": "gcal:event"}))
    conn.execute("INSERT INTO ramp_funds (fund_id, agent, display_name, limit_amount, currency, interval, card_last4, state, created_at, by_user) "
                 "VALUES ('demo-fund', ?, 'Agent · Bookkeeping bot', 200, 'USD', 'MONTHLY', '4417', 'ACTIVE', ?, 'you')", (MAILBOX, at(9, 0, 6)))
    add(conn, at(8, 41), MAILBOX, "log", "purchase", "QuickBooks Online", amount=49.00, currency="USD", reversible=0,
        link="https://app.ramp.com/", note="by credential: Ramp fund 'Agent · Bookkeeping bot' issued to this agent; reversibility: a card purchase can only be refunded by the merchant",
        raw_json=json.dumps({"source": "ramp", "tool": "ramp:transaction", "state": "CLEARED"}))
    add(conn, at(8, 44), MAILBOX, "log", "purchase", "DECLINED: Adobe Creative Cloud", amount=340.00, currency="USD", reversible=1,
        link="https://app.ramp.com/", note="by credential: Ramp fund 'Agent · Bookkeeping bot' issued to this agent; declined by Ramp: AUTHORIZER_CARD_LIMIT; reversibility: nothing was charged",
        raw_json=json.dumps({"source": "ramp", "tool": "ramp:transaction", "state": "DECLINED"}))
    # --- a person's own purchase on the company Ramp account ---
    add(conn, at(12, 15), CARD_AGENT, "card", "purchase", "Elm Street Deli", amount=18.40, currency="USD", reversible=0,
        attribution="human", link="https://app.ramp.com/",
        note="by credential: Ramp card of Sam Ortiz (Harbor Plumbing account), not a fund issued to an agent; reversibility: a card purchase can only be refunded by the merchant",
        raw_json=json.dumps({"source": "ramp", "tool": "ramp:transaction", "state": "CLEARED"}))
    # --- a few earlier days so week/month pages have shape ---
    for d in range(1, 7):
        add(conn, at(9, 30, d), CC, "log", "file_write", f"{SITE}/src/pages/index.html", reversible=1,
            note=CCA + "the file can be edited back", raw_json=raw("Edit", SITE))
        add(conn, at(3, 12, d), COW, "log", "file_write", f"/sessions/daily-report/reports/day-{d}.md", reversible=1,
            note=COWN + "the file can be edited back", raw_json=json.dumps({"tool": "Write", "input": {}, "cwd": "/sessions/daily-report", "session_id": "demo", "source": "cowork", "project": "Daily job report"}))
        add(conn, at(8, 3, d), MAILBOX, "email", "send_email", f"customer{d}@example.com", reversible=0,
            note="by credential: sent from the agent's own mailbox books@harborplumbing.com, subject 'Invoice from Harbor Plumbing'; reversibility: a sent email cannot be recalled",
            raw_json=json.dumps({"source": "email"}), ref=f"demo:mail:day{d}")
    # --- keyboard watch: a person was around from 8:00 until now, except 8:08–8:11 ---
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (at(7, 58), at(8, 8)))
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (at(8, 11), time.time()))
    for d in range(1, 7):
        conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (at(8, 0, d), at(18, 0, d)))
    # typing around the person's lunch purchase and the three invoices
    for base in (at(12, 14), at(8, 1), at(8, 4), at(8, 6)):
        for i in range(6):
            conn.execute("INSERT INTO input_events (timestamp, kind) VALUES (?, 'key')", (base + 20 + i * 2,))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('integrity', ?)", (json.dumps(
        {"checked_at": time.time() - 1800, "transcripts": 23, "bytes": 412 * 1048576, "modified": [], "missing": []}),))
    conn.commit()
    set_nickname(conn, MAILBOX, "Bookkeeping bot", "you")
    ramp_record(conn, "issue", MAILBOX, "you", "Gave Bookkeeping bot a Ramp card: 200.00 USD per monthly, card •••• 4417", 1,
                "the fund can be suspended or terminated from the Stop page", {"fund_id": "demo-fund"})
    conn.execute("UPDATE actions SET timestamp = ? WHERE agent = 'agent receipt'", (at(9, 0, 6),))
    conn.commit()
    correlate(conn)
    evaluate(conn)
    conn.close()


if __name__ == "__main__":
    build()
    print(f"Demo receipt built: {DB}")
    from statement import create_app
    print(f"Serving the demo at http://127.0.0.1:{PORT}/  (Ctrl+C to stop; the real app on 8765 is untouched)")
    create_app(DB, ROOT / "summaries-demo").run(host="127.0.0.1", port=PORT, debug=False)

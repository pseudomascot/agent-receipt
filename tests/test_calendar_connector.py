import json
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from calendar_connector import sync  # noqa: E402
from config import calendar_settings  # noqa: E402
from correlator import correlate  # noqa: E402
from describe import describe  # noqa: E402
from log_parser import INBOX, ingest_all  # noqa: E402
from store import connect  # noqa: E402

NOW = time.time()
SETTINGS = {"calendars": None, "agent": "calendar"}


def _ev(i, title, start, created, modified=None, calendar="Work"):
    return {"id": f"ev{i}", "title": title, "start": start, "end": start + 3600, "all_day": False,
            "calendar": calendar, "location": None, "attendees": [], "created": created,
            "modified": modified or created}


def test_first_run_is_baseline_then_changes_are_recorded(tmp_path):
    conn = connect(tmp_path / "t.db")
    existing = [_ev(1, "Standup", NOW + 3600, NOW - 86400), _ev(2, "Dentist", NOW + 7200, NOW - 3600)]
    assert sync(conn, SETTINGS, existing) == {"created": 0, "changed": 0, "deleted": 0, "tracked": 2}
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0

    later = [existing[0], dict(existing[1], title="Dentist (moved)", modified=NOW - 10),
             _ev(3, "Agent-booked call", NOW + 10000, NOW - 60)]
    assert sync(conn, SETTINGS, later, user="marc") == {"created": 1, "changed": 1, "deleted": 0, "tracked": 3}
    rows = conn.execute("SELECT source, action_type, target, reversible, source_ref, timestamp FROM actions ORDER BY id").fetchall()
    assert rows[0][:4] == ("calendar", "other", "Changed: Dentist (moved) — " + time.strftime("%a %b %d %H:%M", time.localtime(NOW + 7200)), 1)
    assert rows[1][:2] == ("calendar", "create_event") and rows[1][2].startswith("Agent-booked call — ")
    assert rows[1][4] == "cal:ev3" and rows[1][5] == NOW - 60

    # Same state again: nothing new.
    assert sync(conn, SETTINGS, later)["created"] == 0 and conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 2

    # Event 1 disappears.
    assert sync(conn, SETTINGS, later[1:]) == {"created": 0, "changed": 0, "deleted": 1, "tracked": 2}
    row = conn.execute("SELECT action_type, target, reversible, raw_json FROM actions ORDER BY id DESC LIMIT 1").fetchone()
    assert row[:3] == ("other", "Deleted: Standup", 0)
    assert json.loads(row[3])["tool"] == "calendar:deleted"
    assert describe("other", row[1], "calendar:deleted") == "Deleted a calendar event: Standup"
    assert describe("other", rows[0][2], "calendar:changed").startswith("Changed a calendar event: Dentist (moved)")
    conn.close()


def test_old_missing_events_are_not_deletions(tmp_path):
    conn = connect(tmp_path / "t.db")
    old = _ev(9, "Long ago", NOW - 40 * 86400, NOW - 41 * 86400)
    sync(conn, SETTINGS, [old])
    assert sync(conn, SETTINGS, [])["deleted"] == 0             # fell out of the 30-day window, not deleted
    conn.close()


def test_attribution_and_declaration(tmp_path):
    conn = connect(tmp_path / "t.db")
    sync(conn, SETTINGS, [])                                     # baseline (empty)
    sync(conn, SETTINGS, [_ev(5, "Booked by bot", NOW + 5000, NOW - 30)])
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (NOW - 600, NOW + 60))
    conn.commit()
    correlate(conn)
    assert conn.execute("SELECT attribution FROM actions").fetchone()[0] == "unknown"
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "bot.jsonl").write_text(json.dumps({"ts": NOW - 30, "agent": "booking-bot", "action": "create_event",
                                                 "target": "Booked by bot", "id": "cal:ev5"}) + "\n")
    assert ingest_all(conn, (replace(INBOX, root=inbox),)) == 0
    assert conn.execute("SELECT source, attribution, agent FROM actions").fetchone() == ("log", "agent", "booking-bot")
    conn.close()


def test_settings_and_migration(tmp_path):
    assert calendar_settings({}) is None
    assert calendar_settings({"RECEIPT_CALENDAR": "on"}) == {"calendars": None, "agent": "calendar"}
    assert calendar_settings({"RECEIPT_CALENDAR": "yes", "RECEIPT_CALENDARS": "Work, Home"})["calendars"] == ["Work", "Home"]

    # An older database whose CHECK constraint lacks 'calendar' (and whose user column was appended later).
    old = sqlite3.connect(tmp_path / "old.db")
    old.executescript("""
        CREATE TABLE actions (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, agent TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('log','input','email','card','wallet')), action_type TEXT NOT NULL,
            target TEXT, amount REAL, currency TEXT, artifact_link TEXT, reversible INTEGER, attribution TEXT NOT NULL,
            confidence_note TEXT, raw_json TEXT, source_ref TEXT UNIQUE, user TEXT);
        INSERT INTO actions (timestamp, agent, source, action_type, attribution, user, source_ref)
            VALUES (1, 'a', 'log', 'other', 'agent', 'marc', 'r1');
    """)
    old.commit(); old.close()
    conn = connect(tmp_path / "old.db")
    assert "'calendar'" in conn.execute("SELECT sql FROM sqlite_master WHERE name = 'actions'").fetchone()[0]
    assert conn.execute("SELECT agent, user, source_ref FROM actions").fetchone() == ("a", "marc", "r1")
    conn.execute("INSERT INTO actions (timestamp, agent, source, action_type, attribution) VALUES (2, 'c', 'calendar', 'create_event', 'unknown')")
    conn.commit()
    conn.close()

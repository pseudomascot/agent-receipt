import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from doctor import report, status  # noqa: E402
from log_parser import CLAUDE_CODE, CODEX, INBOX  # noqa: E402
from statement import create_app  # noqa: E402
from store import connect  # noqa: E402


def _sources(tmp_path):
    cc = tmp_path / "claude"
    cc.mkdir()
    (cc / "s.jsonl").write_text('{"type": "assistant"}\n' * 3)
    return (replace(CLAUDE_CODE, root=cc), replace(CODEX, root=tmp_path / "missing"), replace(INBOX, root=tmp_path / "inbox"))


def test_status_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr("doctor.email_configured", lambda: False)
    monkeypatch.setattr("doctor.stripe_configured", lambda: True)
    monkeypatch.setattr("doctor.accessibility_granted", lambda: False)
    s = status(tmp_path / "none.db", _sources(tmp_path))
    by = {x["name"]: x for x in s["sources"]}
    assert by["claude-code"]["files"] == 1 and by["claude-code"]["mb"] == 0.0
    assert by["codex"]["files"] == 0 and by["codex"]["exists"] is False
    assert s["db"]["exists"] is False and s["stripe"] is True and s["email"] is False
    text = report(s)
    assert "✓ Claude Code: 1 transcript(s)" in text
    assert "Codex (OpenAI): not found" in text
    assert "NOT granted" in text and "Database: not created yet" in text
    assert "Stripe: configured" in text and "Agent mailbox (email + card alerts): not configured" in text


def test_status_reads_database(tmp_path, monkeypatch):
    monkeypatch.setattr("doctor.accessibility_granted", lambda: None)
    db = tmp_path / "t.db"
    conn = connect(db)
    conn.execute("INSERT INTO actions (timestamp, agent, source, action_type, attribution) VALUES (1, 'a', 'log', 'other', 'agent')")
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (time.time() - 60, time.time()))
    conn.execute("INSERT INTO meta (key, value) VALUES ('integrity', ?)", (json.dumps({"checked_at": 1}),))
    conn.commit()
    conn.close()
    s = status(db, _sources(tmp_path))
    assert s["db"] == {"exists": True, "actions": 1, "needs_review": 0, "monitor_running": True,
                       "monitor_last_seen": s["db"]["monitor_last_seen"], "integrity_checked": True}
    assert "Runner: input monitor is running now." in report(s)
    assert "could not check" in report(s)


def test_front_page_shows_machine_status(tmp_path):
    db = tmp_path / "t.db"
    connect(db).close()
    html = create_app(db).test_client().get("/").get_data(as_text=True)
    assert "This machine" in html
    assert "Keyboard &amp; mouse watch" in html and "not running" in html
    assert "Claude Code" in html

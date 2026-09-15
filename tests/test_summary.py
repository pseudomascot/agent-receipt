import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from statement import create_app  # noqa: E402
from store import connect  # noqa: E402
from summary import build_summary, write_summary  # noqa: E402

DAY = date(2026, 9, 14)
T = datetime(2026, 9, 14, 10, 30).timestamp()


def _seed(db_path):
    conn = connect(db_path)
    conn.executemany(
        "INSERT INTO actions (timestamp, agent, source, action_type, target, amount, currency, attribution, confidence_note) "
        "VALUES (?, 'claude-code', 'log', ?, ?, ?, ?, ?, ?)",
        [
            (T, "file_write", "/tmp/a.txt", None, None, "agent", "x | agent log; no physical input in prior 30s"),
            (T + 1, "file_write", "/tmp/a.txt", None, None, "agent", "x | agent log; input monitor was not running at the time"),
            (T + 2, "execute", "ls", None, None, "agent", "x | agent log; input monitor was not running at the time"),
            (T + 3, "purchase", "shop", 12.5, "USD", "unknown", "no physical input and no agent log"),
        ],
    )
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (T - 60, T + 60))
    conn.commit()
    return conn


def test_build_summary_text(tmp_path, monkeypatch):
    monkeypatch.setattr("config.email_configured", lambda: False)   # independent of this machine's .env
    monkeypatch.setattr("config.stripe_configured", lambda: False)
    monkeypatch.setattr("config.calendar_configured", lambda: False)
    conn = _seed(tmp_path / "t.db")
    text = build_summary(conn, DAY)
    assert text.splitlines() == [
        "Agent Receipt — 2026-09-14",
        "4 actions: 3 agent, 0 human, 1 unknown.",
        "By type: 2 file edits, 1 command run, 1 purchase.",
        "Projects: (unknown project) (4).",
        "Money: 12.50 USD.",
        "Most-written files: a.txt (2x).",
        "Keyboard watch: about 2 minutes (10:29–10:31).",
        "2 of 4 actions happened while it wasn't watching.",
        "Unknown: 1 action(s) nobody can be confirmed for — review them.",
        "Needs review: none.",
        "Not covered: Google Antigravity; Claude Desktop chat (not the Code tab); "
        "Calendar (the macOS Calendar app and everything it syncs); "
        "Email sent from the agent's mailbox; Card charges (issuer alert emails); "
        "Card charges (Stripe Issuing) and charges collected via Stripe; Crypto wallet.",
    ]
    conn.close()


def test_build_summary_empty_day(tmp_path):
    conn = connect(tmp_path / "t.db")
    text = build_summary(conn, date(2020, 1, 1))
    assert "No agent actions recorded." in text
    assert "Keyboard watch: off all day." in text
    assert "Unknown: none." in text
    conn.close()


def test_write_summary_creates_file(tmp_path):
    conn = _seed(tmp_path / "t.db")
    path = write_summary(conn, DAY, tmp_path / "out")
    assert path == tmp_path / "out" / "2026-09-14.txt"
    assert path.read_text().startswith("Agent Receipt — 2026-09-14")
    conn.close()


def test_day_page_shows_summary_and_saved_state(tmp_path):
    db = tmp_path / "t.db"
    conn = _seed(db)
    out = tmp_path / "out"
    client = create_app(db, out).test_client()
    html = client.get("/day/2026-09-14").get_data(as_text=True)
    assert "4 actions: 3 agent, 0 human, 1 unknown." in html
    assert "Not yet saved to a file" in html
    assert "<b>2 of 4</b> actions happened while it wasn't watching" in html
    write_summary(conn, DAY, out)
    html = client.get("/day/2026-09-14").get_data(as_text=True)
    assert "Saved to summaries/2026-09-14.txt" in html
    conn.close()

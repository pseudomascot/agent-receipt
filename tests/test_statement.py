import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from statement import create_app  # noqa: E402
from store import connect  # noqa: E402

DAY = "2026-09-14"
T = datetime(2026, 9, 14, 10, 30).timestamp()


def _seed(db_path):
    conn = connect(db_path)
    conn.executemany(
        "INSERT INTO actions (timestamp, agent, source, action_type, target, attribution, confidence_note) "
        "VALUES (?, 'claude-code', 'log', ?, ?, ?, ?)",
        [
            (T, "file_write", "/tmp/<script>alert(1)</script>.txt", "agent", "agent log"),
            (T + 60, "execute", "git commit", "agent", "agent log wins; physical input also present"),
            (T + 120, "other", "https://example.com", "unknown", "no physical input and no agent log"),
            (T - 86400, "post", "yesterday.html", "human", "physical input present"),
        ],
    )
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (T - 600, T + 600))
    conn.commit()
    conn.close()


def test_index_lists_days_with_counts(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    client = create_app(db).test_client()
    html = client.get("/").get_data(as_text=True)
    assert html.count(f"/day/{DAY}") >= 1
    assert "/day/2026-09-13" in html
    assert "Claude Desktop chat" in html and "not covered" in html


def test_day_page_groups_and_escapes(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    client = create_app(db).test_client()
    html = client.get(f"/day/{DAY}").get_data(as_text=True)
    assert "&lt;script&gt;" in html and "<script>alert" not in html
    assert "git commit" in html
    assert "https://example.com" in html            # appears in the Unknown section
    assert "yesterday.html" not in html             # different day
    assert "about 20 minute(s)" in html             # coverage clipped to the day
    assert "10:20&ndash;10:40" in html


def test_day_page_with_no_data(tmp_path):
    db = tmp_path / "t.db"
    connect(db).close()
    client = create_app(db).test_client()
    resp = client.get("/day/2020-01-01")
    assert resp.status_code == 200
    assert "Not running at any point this day" in resp.get_data(as_text=True)


def test_bad_day_is_404(tmp_path):
    db = tmp_path / "t.db"
    connect(db).close()
    assert create_app(db).test_client().get("/day/not-a-date").status_code == 404

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from queries import display_target  # noqa: E402
from statement import create_app  # noqa: E402
from store import connect  # noqa: E402

DAY = "2026-09-14"
T = datetime(2026, 9, 14, 10, 30).timestamp()
LONG_CMD = 'cd "/Users/marc/proj-a" && ' + "python3 build.py --flag " * 8


def _raw(cwd):
    return json.dumps({"tool": "x", "input": {}, "cwd": cwd, "session_id": "s1"})


def _seed(db_path):
    conn = connect(db_path)
    conn.executemany(
        "INSERT INTO actions (timestamp, agent, source, action_type, target, attribution, confidence_note, raw_json) "
        "VALUES (?, 'claude-code', 'log', ?, ?, ?, ?, ?)",
        [
            (T, "file_write", "/Users/marc/proj-a/src/<script>alert(1)</script>.txt", "agent", "agent log", _raw("/Users/marc/proj-a")),
            (T + 60, "execute", LONG_CMD, "agent", "x | agent log wins; physical input also present", _raw("/Users/marc/proj-a")),
            (T + 90, "execute", "git commit -m hi", "agent", "agent log", _raw("/Users/marc/proj-b")),
            (T + 120, "other", "https://example.com", "unknown", "no physical input and no agent log", _raw("/Users/marc/proj-b")),
            (T - 86400, "post", "yesterday.html", "human", "physical input present", _raw(None)),
        ],
    )
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (T - 600, T + 600))
    conn.commit()
    conn.close()


def test_display_target():
    assert display_target("file_write", "/Users/marc/proj-a/src/x.py", "/Users/marc/proj-a") == "src/x.py"
    assert display_target("file_write", "/etc/hosts", "/Users/marc/proj-a") == "/etc/hosts"
    assert display_target("execute", 'cd "/Users/marc/p q" && ls', None) == "ls"
    assert display_target("execute", "cd /tmp && rm x", None) == "rm x"
    assert display_target("execute", "echo cd && ls", None) == "echo cd && ls"
    assert display_target("other", "https://x", "/tmp") == "https://x"


def test_index_lists_days_with_counts_and_projects(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    client = create_app(db).test_client()
    html = client.get("/").get_data(as_text=True)
    assert html.count(f"/day/{DAY}") >= 1
    assert "/day/2026-09-13" in html
    assert "proj-a, proj-b" in html or "proj-b, proj-a" in html
    assert "Claude Desktop chat" in html and "not covered" in html


def test_day_page_groups_by_project_and_escapes(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    client = create_app(db).test_client()
    html = client.get(f"/day/{DAY}").get_data(as_text=True)
    assert "&lt;script&gt;" in html and "<script>alert" not in html
    assert "<h3>proj-a" in html and "<h3>proj-b" in html
    assert "src/&lt;script&gt;alert(1)&lt;/script&gt;.txt" in html   # relative to project
    assert "<details><summary>python3 build.py" in html             # long command collapsed, leading cd stripped
    assert "git commit -m hi" in html
    assert "https://example.com" in html                             # Unknown section
    assert "yesterday.html" not in html
    assert "about 20 minute(s)" in html
    assert "10:20&ndash;10:40" in html


def test_day_page_type_filter(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    client = create_app(db).test_client()
    html = client.get(f"/day/{DAY}?type=execute").get_data(as_text=True)
    assert "git commit -m hi" in html
    assert "src/&lt;script&gt;" not in html                           # file_write row hidden
    assert 'class="chip on">execute (2)' in html
    assert "<b>4</b>actions" in html                                  # totals stay for the whole day
    html = client.get(f"/day/{DAY}?type=bogus").get_data(as_text=True)
    assert "src/&lt;script&gt;" in html                               # bad filter ignored


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

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


def _seed(db_path, second_agent=False):
    conn = connect(db_path)
    conn.executemany(
        "INSERT INTO actions (timestamp, agent, user, source, action_type, target, attribution, confidence_note, raw_json) "
        "VALUES (?, 'claude-code', 'marc', 'log', ?, ?, ?, ?, ?)",
        [
            (T, "file_write", "/Users/marc/proj-a/src/<script>alert(1)</script>.txt", "agent", "agent log", _raw("/Users/marc/proj-a")),
            (T + 60, "execute", LONG_CMD, "agent", "x | agent log wins; physical input also present", _raw("/Users/marc/proj-a")),
            (T + 90, "execute", "git commit -m hi", "agent",
             "declared in transcript; reversibility: git keeps history | agent log", _raw("/Users/marc/proj-b")),
            (T + 120, "other", "https://example.com", "unknown", "no physical input and no agent log", _raw("/Users/marc/proj-b")),
            (T - 86400, "post", "yesterday.html", "human", "physical input present", _raw(None)),
        ],
    )
    if second_agent:
        conn.execute(
            "INSERT INTO actions (timestamp, agent, user, source, action_type, target, attribution, confidence_note, raw_json) "
            "VALUES (?, 'other-bot', 'sam', 'log', 'execute', 'deploy.sh', 'agent', 'agent log', ?)",
            (T + 200, _raw("/Users/marc/proj-b")),
        )
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (T - 600, T + 600))
    conn.commit()
    conn.close()


def test_agent_and_user_filters_appear_only_with_variety(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    html = create_app(db).test_client().get(f"/day/{DAY}").get_data(as_text=True)
    assert "?agent=" not in html                                              # one agent: no agent chips
    assert 'data-toggle-type="execute"' in html                              # category toggles always present

    db2 = tmp_path / "t2.db"
    _seed(db2, second_agent=True)
    client = create_app(db2).test_client()
    html = client.get(f"/day/{DAY}").get_data(as_text=True)
    assert "?agent=other-bot" in html and "?user=sam" in html
    assert "deploy.sh" in html and "git commit -m hi" in html

    html = client.get(f"/day/{DAY}?agent=other-bot").get_data(as_text=True)
    assert "deploy.sh" in html and "git commit -m hi" not in html
    html = client.get(f"/day/{DAY}?user=marc&type=execute").get_data(as_text=True)
    assert "git commit -m hi" in html and "deploy.sh" not in html and "src/&lt;script&gt;" not in html


def test_merge_intervals_and_duration_text():
    from queries import duration_text, merge_intervals
    assert merge_intervals([(0, 100), (130, 200), (1000, 1100), (1050, 1300)]) == [(0, 200), (1000, 1300)]
    assert merge_intervals([(0, 100), (130, 200)], gap=0) == [(0, 100), (130, 200)]
    assert duration_text(90) == "2 minutes" and duration_text(60) == "1 minute"
    assert duration_text(3600) == "1 hour" and duration_text(43680) == "12 hours 8 min"


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
    assert '<details class="screen-only"><summary>python3 build.py' in html   # collapsed, leading cd stripped
    assert "git commit -m hi" in html
    assert "git keeps history" in html                               # reversibility reason shown
    assert "https://example.com" in html                             # Unknown section
    assert "yesterday.html" not in html
    assert "about <b>20 minutes</b>" in html
    assert "10:20&ndash;10:40" in html
    assert "It was watching for every action listed." in html


def test_actions_are_newest_first(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    html = create_app(db).test_client().get(f"/day/{DAY}").get_data(as_text=True)
    # proj-b's latest action (T+90) is newer than proj-a's (T+60): proj-b group comes first.
    assert html.index("<h3>proj-b") < html.index("<h3>proj-a")
    # Within proj-a, the T+60 command is listed above the T file write.
    assert html.index("python3 build.py") < html.index("src/&lt;script&gt;")


def test_day_page_type_filter(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    client = create_app(db).test_client()
    html = client.get(f"/day/{DAY}?type=execute").get_data(as_text=True)
    assert "git commit -m hi" in html
    assert "src/&lt;script&gt;" not in html                           # file_write row hidden
    assert "Filtered: Commands run" in html and "2 of 4 actions" in html
    assert 'id="actions"' in html
    assert '<b>4</b><span class="label">actions</span>' in html       # totals stay for the whole day
    html = client.get(f"/day/{DAY}?type=bogus").get_data(as_text=True)
    assert "src/&lt;script&gt;" in html                               # bad filter ignored


def test_range_week_month_all_pages(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    client = create_app(db).test_client()
    html = client.get("/range/2026-09-14/2026-09-13").get_data(as_text=True)   # reversed order is fine
    assert "2026-09-13 to 2026-09-14" in html and "2 days" in html
    assert '<b>5</b><span class="label">actions</span>' in html                 # both days counted
    assert "yesterday.html" in html and "git commit -m hi" in html
    assert 'href="/day/2026-09-13" class="daychip"><span>2026-09-13</span><b>1</b>' in html   # by-day grid
    assert html.index('data-section="actions"') < html.index('data-section="byday"') < html.index('data-section="keyboard"')
    assert "/export.csv?start=2026-09-13&end=2026-09-14" in html
    html = client.get("/range/2026-09-13/2026-09-14?type=post").get_data(as_text=True)
    assert "yesterday.html" in html and "git commit -m hi" not in html
    assert client.get("/week").status_code == 200
    assert client.get("/month").status_code == 200
    html = client.get("/all").get_data(as_text=True)
    assert "2026-09-13 to" in html
    assert client.get("/range/bad/2026-09-14").status_code == 404


def test_alerts_page_flagging_and_mark_seen(tmp_path):
    from alerts import evaluate
    db = tmp_path / "t.db"
    _seed(db)
    conn = connect(db)
    conn.execute("UPDATE actions SET reversible = 0, agent = 'cowork (scheduled task)' WHERE target = 'git commit -m hi'")
    conn.commit()
    evaluate(conn)
    conn.close()
    client = create_app(db).test_client()

    # Two alerts: the scheduled irreversible commit, and the seeded unknown-attribution row.
    html = client.get(f"/day/{DAY}").get_data(as_text=True)
    assert "2 need review</a>" in html
    assert 'class="flagged"' in html and "unattended_irreversible" in html
    assert "Needs review: 2 open alert(s)." in html                            # summary line

    html = client.get("/alerts").get_data(as_text=True)
    assert "git commit -m hi" in html and "Mark all 2 seen" in html
    ids = [part.split('"')[0] for part in html.split('name="id" value="')[1:]]
    assert len(ids) == 2

    resp = client.post("/alerts/seen", data={"id": ids[0]})                   # newest first: the unknown row
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/alerts")
    assert "1 needs review</a>" in client.get(f"/day/{DAY}").get_data(as_text=True)

    client.post("/alerts/seen", data={"all": "1"})
    assert "Nothing waiting" in client.get("/alerts").get_data(as_text=True)
    html = client.get(f"/day/{DAY}").get_data(as_text=True)
    assert "Nothing needs review</a>" in html and 'class="flagged"' not in html


def test_web_links_are_clickable(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    conn = connect(db)
    conn.execute("UPDATE actions SET artifact_link = 'https://calendar.google.com/event?eid=abc' WHERE target = 'git commit -m hi'")
    conn.execute("UPDATE actions SET artifact_link = 'file:///tmp/x' WHERE target = 'https://example.com'")
    conn.commit()
    conn.close()
    html = create_app(db).test_client().get(f"/day/{DAY}").get_data(as_text=True)
    assert '<a href="https://calendar.google.com/event?eid=abc" target="_blank" rel="noopener">Open ↗</a>' in html
    assert 'href="file:///tmp/x"' not in html


def test_print_markup_present(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    html = create_app(db).test_client().get(f"/day/{DAY}").get_data(as_text=True)
    assert "@media print" in html
    assert '<pre class="full print-only">' in html                              # long command printed in full
    assert 'class="print-only note">Statement for 2026-09-14' in html


def test_day_page_with_no_data(tmp_path):
    db = tmp_path / "t.db"
    connect(db).close()
    client = create_app(db).test_client()
    resp = client.get("/day/2020-01-01")
    assert resp.status_code == 200
    assert "wasn't watching the keyboard and mouse on this day" in resp.get_data(as_text=True)


def test_bad_day_is_404(tmp_path):
    db = tmp_path / "t.db"
    connect(db).close()
    assert create_app(db).test_client().get("/day/not-a-date").status_code == 404

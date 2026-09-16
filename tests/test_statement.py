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
    assert 'class="chip">other-bot' in html or ">other-bot (1)</a>" in html                 # few agents: chips
    assert 'name="q"' in html and "<select" in html and 'proj-a (2)</option>' in html        # projects: a dropdown
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
    html = client.get(f"/day/{DAY}?group=project").get_data(as_text=True)
    assert "&lt;script&gt;" in html and "<script>alert" not in html
    assert "<h3>proj-a" in html and "<h3>proj-b" in html
    assert "src/&lt;script&gt;alert(1)&lt;/script&gt;.txt" in html   # relative to project
    assert '<details class="screen-only" open><summary>python3 build.py' in html   # in the details row, leading cd stripped
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
    client = create_app(db).test_client()
    html = client.get(f"/day/{DAY}?group=project").get_data(as_text=True)
    # proj-b's latest action (T+90) is newer than proj-a's (T+60): proj-b group comes first.
    assert html.index("<h3>proj-b") < html.index("<h3>proj-a")
    # Within proj-a, the T+60 command is listed above the T file write.
    assert html.index("python3 build.py") < html.index("src/&lt;script&gt;")
    # Default: one flat list, newest first across projects, with a Project column.
    html = client.get(f"/day/{DAY}").get_data(as_text=True)
    assert "<h3>proj-a" not in html and "<th>Project</th>" in html
    assert html.index("git commit -m hi") < html.index("python3 build.py") < html.index("src/&lt;script&gt;")
    assert 'title="proj-b">proj-b</td>' in html
    assert 'href="/day/2026-09-14?group=project#actions" class="chip check off"' in html        # the toggle, off
    html = client.get(f"/day/{DAY}?group=project&type=execute").get_data(as_text=True)
    assert "<h3>proj-a" in html and 'href="/day/2026-09-14?type=execute#actions" class="chip check"' in html   # toggle keeps the filter


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
    assert 'data-tile-type="execute"' in html and 'data-tile-all' in html   # tiles are shortcuts
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
    assert 'class="row flagged"' in html and "unattended irreversible" in html
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
    assert "Nothing needs review</a>" in html and 'row flagged"' not in html


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


def test_subagent_rows_are_tagged(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    conn = connect(db)
    conn.execute("INSERT INTO actions (timestamp, agent, user, source, action_type, target, attribution, confidence_note, raw_json) "
                 "VALUES (?, 'claude-code / Explore: tidy tests', 'marc', 'log', 'file_write', 'w.py', 'agent', 'agent log', ?)",
                 (T + 30, _raw("/Users/marc/proj-a")))
    conn.commit()
    conn.close()
    html = create_app(db).test_client().get(f"/day/{DAY}").get_data(as_text=True)
    assert 'title="claude-code / Explore: tidy tests">Sub-agent of Claude Code (1)</a>' in html   # plain-named chip
    assert 'title="claude-code / Explore: tidy tests · sub-agent · tidy tests">Sub-agent of Claude Code</span>' in html
    assert html.count("· sub-agent · ") == 1                                    # kind lives in the tooltip, once
    assert html.count('· local agent">') >= 3 and 'title="claude-code · local agent">Claude Code</span>' in html
    assert html.count("Who did it</th>") == 1 and ">Who</th>" not in html          # one flat table, no second Who column


def test_summary_is_linked_and_project_filter_works(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    client = create_app(db).test_client()
    html = client.get(f"/day/{DAY}").get_data(as_text=True)
    assert 'href="/day/2026-09-14?project=proj-a#actions">proj-a (2)</a>' in html          # summary project link
    assert 'href="/day/2026-09-14?type=execute#actions">2 commands run</a>' in html         # summary type link
    assert 'href="/money/2026-09-14/2026-09-14">' not in html and "none recorded" in html  # no money that day
    assert 'href="/alerts">none</a>' in html and 'href="#coverage">' in html
    assert '<pre class="summary print-only">' in html                                       # plain text kept for print
    html = client.get(f"/day/{DAY}?project=proj-a").get_data(as_text=True)
    assert "src/&lt;script&gt;" in html and "git commit -m hi" not in html
    assert "Filtered:" in html and "project: proj-a" in html and "2 of 4 actions" in html
    assert 'href="/day/2026-09-14?type=execute&project=proj-a#actions"' in html            # type link keeps the project
    assert "/export.csv?start=2026-09-14&end=2026-09-14&amp;project=proj-a" in html   # literal & stays, variable & is escaped


def test_summary_folds_long_lists(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    for i in range(8):
        conn.execute("INSERT INTO actions (timestamp, agent, user, source, action_type, target, attribution, confidence_note, raw_json) "
                     "VALUES (?, ?, 'marc', 'log', 'file_write', 'f', 'agent', 'agent log', ?)", (T + i, f"bot-{i}", _raw(f"/Users/marc/p{i}")))
    conn.commit(); conn.close()
    html = create_app(db).test_client().get(f"/day/{DAY}").get_data(as_text=True)
    assert html.count('data-more>+3 more</a>') == 2                                    # projects and agents fold after 5
    assert '<span class="more hidden">, <a href="/day/2026-09-14?project=' in html and 'p7 (1)</a>' in html
    assert "Log integrity: Log integrity" not in html


def test_keyword_search(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    client = create_app(db).test_client()
    html = client.get(f"/day/{DAY}?q=commit").get_data(as_text=True)
    assert "git commit -m hi" in html and "python3 build.py" not in html and "src/&lt;script&gt;" not in html
    assert "search “commit”" in html and "1 of 4 actions" in html and 'name="q" value="commit"' in html
    html = client.get(f"/day/{DAY}?q=PROJ-A%20build").get_data(as_text=True)               # all words, any case, any field
    assert "python3 build.py" in html and "git commit -m hi" not in html
    assert client.get(f"/day/{DAY}?q=zzz-nothing").get_data(as_text=True).count("No actions") >= 1
    html = client.get(f"/day/{DAY}?q=commit&type=execute").get_data(as_text=True)
    assert 'name="type" value="execute"' in html and 'name="q" value="commit"' in html         # search keeps the type filter, and vice versa
    assert "1 of 4 actions" in html


def test_many_agents_become_a_dropdown(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    for i in range(8):
        conn.execute("INSERT INTO actions (timestamp, agent, user, source, action_type, target, attribution, confidence_note, raw_json) "
                     "VALUES (?, ?, 'marc', 'log', 'file_write', 'f', 'agent', 'agent log', ?)", (T + i, f"bot-{i}", _raw("/Users/marc/p")))
    conn.commit(); conn.close()
    html = create_app(db).test_client().get(f"/day/{DAY}?type=file_write").get_data(as_text=True)
    assert html.count("<select class=\"pick\"") == 1                                        # agents (8) → dropdown; one project → no project picker
    assert '?agent=bot-3&type=file_write#actions" >bot-3 (1)</option>' in html or 'bot-3 (1)</option>' in html
    assert 'class="chip on">all</a>' not in html

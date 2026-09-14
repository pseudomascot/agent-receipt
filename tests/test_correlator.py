import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from correlator import attribute, correlate  # noqa: E402
from store import connect  # noqa: E402

W = 30.0


def test_attribute_log_source_always_agent():
    assert attribute("log", True, 5, False, W)[0] == "agent"
    assert attribute("log", True, 0, False, W)[0] == "agent"
    assert attribute("log", False, 0, False, W) == ("agent", "agent log; input monitor was not running at the time")
    assert "physical input also present (5 events" in attribute("log", True, 5, False, W)[1]


def test_attribute_non_log_source():
    assert attribute("email", False, 3, False, W)[0] == "unknown"
    assert attribute("email", True, 3, True, W)[0] == "agent"
    assert attribute("email", True, 3, False, W)[0] == "human"
    assert attribute("email", True, 0, False, W)[0] == "unknown"


def _add_action(conn, ts, source, note="declared somewhere"):
    cur = conn.execute(
        "INSERT INTO actions (timestamp, agent, source, action_type, attribution, confidence_note) "
        "VALUES (?, 'x', ?, 'other', 'unknown', ?)",
        (ts, source, note),
    )
    return cur.lastrowid


def test_correlate_end_to_end(tmp_path):
    conn = connect(tmp_path / "t.db")
    # Monitor ran from t=1000 to t=2000.
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', 1000, 2000)")
    # Physical input at t=1290/1295 (no agent log nearby) and t=1490/1495 (agent log at 1500).
    conn.executemany("INSERT INTO input_events (timestamp, kind) VALUES (?, 'key')",
                     [(1290,), (1295,), (1490,), (1495,)])

    log_with_input = _add_action(conn, 1500, "log")
    log_without_input = _add_action(conn, 1800, "log")
    log_uncovered = _add_action(conn, 3000, "log")
    email_human = _add_action(conn, 1300, "email", note="")
    email_agent = _add_action(conn, 1801, "email")      # log action at 1800 within window
    email_unknown = _add_action(conn, 1900, "email")
    email_uncovered = _add_action(conn, 3001, "email")
    conn.commit()

    counts = correlate(conn, W)
    assert counts == {"agent": 4, "human": 1, "unknown": 2}

    def row(i):
        return conn.execute("SELECT attribution, confidence_note FROM actions WHERE id = ?", (i,)).fetchone()

    assert row(log_with_input) == ("agent", "declared somewhere | agent log wins; physical input also present (2 events in prior 30s)")
    assert row(log_without_input) == ("agent", "declared somewhere | agent log; no physical input in prior 30s")
    assert row(log_uncovered)[1].endswith("input monitor was not running at the time")
    assert row(email_human) == ("human", "physical input present (2 events in prior 30s), no agent log entry")
    assert row(email_agent)[0] == "agent"
    assert row(email_unknown)[0] == "unknown"
    assert row(email_uncovered) == ("unknown", "declared somewhere | input monitor was not running at the time")
    conn.close()


def test_correlate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "t.db")
    _add_action(conn, 1500, "log")
    conn.commit()
    correlate(conn, W)
    correlate(conn, W)
    note = conn.execute("SELECT confidence_note FROM actions").fetchone()[0]
    assert note.count(" | ") == 1
    conn.close()

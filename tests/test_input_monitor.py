import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from input_monitor import init_db, log_event  # noqa: E402


def test_init_db_creates_expected_schema(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    cols = conn.execute("PRAGMA table_info(input_events)").fetchall()
    col_names = {c[1] for c in cols}
    assert col_names == {"id", "timestamp", "kind"}
    conn.close()


def test_log_event_records_key_and_click(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    log_event(conn, "key")
    log_event(conn, "click")
    rows = conn.execute("SELECT kind FROM input_events ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["key", "click"]
    conn.close()


def test_log_event_rejects_unknown_kind(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    try:
        log_event(conn, "mousemove")
        assert False, "expected ValueError"
    except ValueError:
        pass
    conn.close()


def test_db_schema_has_no_extra_columns_for_content():
    # Guard against ever accidentally adding key values, positions, etc.
    import inspect
    source = inspect.getsource(init_db)
    for forbidden in ("key_code", "position", "window", "x_coord", "y_coord"):
        assert forbidden not in source

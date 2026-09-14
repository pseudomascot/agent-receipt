import csv
import hashlib
import io
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from export import COLUMNS, export_csv  # noqa: E402
from queries import statement  # noqa: E402
from statement import create_app  # noqa: E402
from store import connect  # noqa: E402

D1, D2 = date(2026, 9, 13), date(2026, 9, 14)
T1 = datetime(2026, 9, 13, 9, 0).timestamp()
T2 = datetime(2026, 9, 14, 10, 30).timestamp()
LONG = "x" * 700


def _seed(db_path):
    conn = connect(db_path)
    conn.executemany(
        "INSERT INTO actions (timestamp, agent, user, source, action_type, target, attribution, confidence_note, source_ref) "
        "VALUES (?, ?, 'marc', 'log', ?, ?, 'agent', 'note; reversibility: r | agent log', ?)",
        [
            (T1, "claude-code", "execute", 'a "quoted", comma', "ref1"),
            (T2, "cowork", "file_write", LONG, "ref2"),
            (T2 + 5, "cowork", "execute", "rm x", "ref3"),
        ],
    )
    conn.commit()
    return conn


def test_export_rows_footer_and_checksum(tmp_path):
    conn = _seed(tmp_path / "t.db")
    stats = statement(conn, D1, D2)
    text = export_csv(conn, stats, {"type": None, "agent": None, "user": None})
    data_part, footer_part = text.split("\n\n", 1)
    rows = list(csv.reader(io.StringIO(data_part)))
    assert rows[0] == COLUMNS
    assert len(rows) == 4
    assert rows[1][COLUMNS.index("target")] == 'a "quoted", comma'
    assert rows[2][COLUMNS.index("target")] == LONG                    # full target, not the clipped one
    assert rows[2][COLUMNS.index("reversible_reason")] == "r"
    assert rows[3][COLUMNS.index("source_ref")] == "ref3"

    digest = hashlib.sha256((data_part + "\n").encode()).hexdigest()
    assert f"# sha256 of the data section (first 4 lines),{digest}" in footer_part
    assert "# verify,head -n 4 <this file> | shasum -a 256" in footer_part
    assert "# coverage: Claude Desktop chat (not the Code tab),not covered" in footer_part
    assert "# rows,3" in footer_part
    conn.close()


def test_export_route_honors_filters_and_names_file(tmp_path):
    db = tmp_path / "t.db"
    _seed(db).close()
    client = create_app(db).test_client()
    resp = client.get("/export.csv?start=2026-09-13&end=2026-09-14&agent=cowork&type=execute")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert 'filename="agent-receipt_2026-09-13_to_2026-09-14.csv"' in resp.headers["Content-Disposition"]
    body = resp.get_data(as_text=True)
    assert "rm x" in body and "quoted" not in body and LONG not in body
    assert "# filters,\"type=execute, agent=cowork\"" in body or "# filters,type=execute, agent=cowork" in body
    assert client.get("/export.csv?start=nope&end=2026-09-14").status_code == 404

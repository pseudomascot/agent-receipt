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


def _seed_money(db_path):
    conn = connect(db_path)
    conn.executemany(
        "INSERT INTO actions (timestamp, agent, user, source, action_type, target, amount, currency, attribution, confidence_note, source_ref, raw_json) "
        "VALUES (?, ?, 'marc', ?, ?, ?, ?, ?, ?, 'n', ?, ?)",
        [
            (T1, "cowork", "card", "purchase", "AMAZON *MKTPL", 12.5, "USD", "human", "card:1", '{"cwd": "/Users/marc/acme"}'),
            (T2, "stripe", "card", "other", "Charge ch_1 succeeded", 49.0, "usd", "agent", "stripe:ch_1", "{}"),
            (T2 + 5, "cowork", "card", "purchase", "Coffee, \"fancy\"", 4.25, "EUR", "unknown", "card:2", "{}"),
            (T2 + 9, "cowork", "log", "execute", "make deploy-xyz", None, None, "agent", "ref9", "{}"),
        ],
    )
    conn.commit()
    return conn


def test_money_summary_and_quickbooks_csv(tmp_path):
    from export import MONEY_COLUMNS, money_csv
    from queries import money_summary
    conn = _seed_money(tmp_path / "t.db")
    money = money_summary(statement(conn, D1, D2))
    conn.close()
    assert [r["source_ref"] for r in money["rows"]] == ["card:2", "stripe:ch_1", "card:1"]   # newest first
    assert money["currencies"] == ["USD", "EUR"]
    assert money["totals"]["USD"] == {"in": 49.0, "out": 12.5, "net": 36.5, "count": 2}
    assert [m[0] for m in money["months"]] == ["September 2026"]

    text = money_csv(money, "USD")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == MONEY_COLUMNS
    assert len(rows) == 3 and "#" not in text                                # chronological, no footer
    assert rows[1][0] == "09/13/2026" and rows[1][2] == "-12.50"               # money out is negative
    assert "agent: cowork" in rows[1][1] and "project: acme" in rows[1][1] and "ref: card:1" in rows[1][1]
    assert rows[2][0] == "09/14/2026" and rows[2][2] == "49.00"
    assert rows[2][1].startswith("Received: Charge ch_1") and "unknown project" not in rows[2][1]
    eur = list(csv.reader(io.StringIO(money_csv(money, "EUR"))))
    assert len(eur) == 2 and eur[1][2] == "-4.25" and 'Coffee, "fancy"' in eur[1][1]


def test_money_page_and_export_route(tmp_path):
    db = tmp_path / "t.db"
    _seed_money(db).close()
    client = create_app(db).test_client()
    html = client.get("/money").get_data(as_text=True)                          # all time: earliest day .. today
    assert "September 2026" in html and "AMAZON" in html and "deploy-xyz" not in html
    today = date.today().isoformat()
    assert f"/export-money.csv?start=2026-09-13&end={today}&currency=USD" in html
    assert f"/export-money.csv?start=2026-09-13&end={today}&currency=EUR" in html
    assert "36.50" in html and "USD net" in html
    assert 'href="/money" class="on"' in html

    resp = client.get("/export-money.csv?start=2026-09-13&end=2026-09-14")     # default: commonest currency
    assert resp.mimetype == "text/csv"
    assert 'filename="agent-receipt_money_USD_2026-09-13_to_2026-09-14.csv"' in resp.headers["Content-Disposition"]
    assert resp.get_data(as_text=True).startswith("Date,Description,Amount\n09/13/2026,")

    html = client.get("/money/2026-09-14/2026-09-14").get_data(as_text=True)
    assert "AMAZON" not in html and "Charge ch_1" in html
    assert client.get("/money/2026-09-14/nope").status_code == 404
    empty = create_app(tmp_path / "e.db").test_client().get("/money").get_data(as_text=True)
    assert "No money movements" in empty

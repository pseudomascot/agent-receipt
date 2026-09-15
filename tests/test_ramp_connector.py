import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import ramp_connector as rc  # noqa: E402
from agents import RECEIPT_AGENT, list_agents  # noqa: E402
from config import ramp_settings  # noqa: E402
from correlator import correlate  # noqa: E402
from statement import create_app  # noqa: E402
from stop import controls, run as stop_run  # noqa: E402
from store import connect  # noqa: E402

ENV = {"RECEIPT_RAMP_CLIENT_ID": "cid", "RECEIPT_RAMP_CLIENT_SECRET": "csecretzz", "RECEIPT_RAMP_USER_ID": "user-1"}


def test_settings_default_to_sandbox():
    s = ramp_settings(ENV)
    assert s["mode"] == "sandbox" and s["api"] == "https://demo-api.ramp.com" and s["app"] == "https://demo.ramp.com"
    assert s["agent"] == "ramp card (sandbox)" and s["user_id"] == "user-1" and "funds:write" in s["scopes"]
    assert ramp_settings({**ENV, "RECEIPT_RAMP_ENV": "production"})["api"] == "https://api.ramp.com"
    assert ramp_settings({}) is None and ramp_settings({"RECEIPT_RAMP_CLIENT_ID": "PASTE-CLIENT-ID", "RECEIPT_RAMP_CLIENT_SECRET": "x"}) is None


def test_token_and_api_use_basic_auth_json_and_bearer():
    calls = []
    def http(url, method, headers, data):
        from urllib.parse import parse_qs
        parsed = None
        if data:
            parsed = json.loads(data) if headers.get("Content-Type") == "application/json" else {k: v[0] for k, v in parse_qs(data.decode()).items()}
        calls.append((url, method, headers, parsed))
        if url.endswith("/token"):
            return {"access_token": "tok-1", "expires_in": 3600}
        return {"data": [{"id": "x"}], "page": {"next": None}}
    rc._tokens.clear()
    s = ramp_settings(ENV)
    out = rc.api(s, "GET", "/developer/v1/funds", {"page_size": 100}, http=http)
    assert out["data"] == [{"id": "x"}]
    tok_url, tok_method, tok_headers, tok_body = calls[0]
    assert tok_url == "https://demo-api.ramp.com/developer/v1/token" and tok_method == "POST"
    assert tok_headers["Authorization"].startswith("Basic ") and tok_headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert tok_body == {"grant_type": "client_credentials", "scope": s["scopes"]}
    url, method, headers, body = calls[1]
    assert url == "https://demo-api.ramp.com/developer/v1/funds?page_size=100" and headers["Authorization"] == "Bearer tok-1"
    rc.api(s, "GET", "/developer/v1/users", http=http)
    assert len([c for c in calls if c[0].endswith("/token")]) == 1                      # cached
    rc._tokens.clear()


def _fake(responses: dict, calls: list):
    """Fake `api(settings, method, path, params=None, body=None, idempotent=False)`."""
    def call(settings, method, path, params=None, body=None, idempotent=False):
        calls.append((method, path, params, body, idempotent))
        key = f"{method} {path}"
        value = responses.get(key)
        if callable(value):
            return value(params, body)
        if value is None:
            raise rc.RampError(404, {"error": f"no fake for {key}"})
        return value
    return call


def test_issue_fund_records_mapping_and_receipt_row(tmp_path):
    conn = connect(tmp_path / "t.db")
    calls = []
    fund_resp = {"id": "fund-1", "state": "ACTIVE", "display_name": "Agent · Bookkeeping bot",
                 "cards": [{"card_id": "card-1", "last_four": "4242", "display_name": "Agent · Bookkeeping bot"}]}
    call = _fake({"POST /developer/v1/funds": fund_resp}, calls)
    s = ramp_settings(ENV)
    out = rc.issue_fund(conn, s, "mailbox bot@x.com", "Bookkeeping bot", 500, "monthly", "marc", call=call)
    assert out == {"fund_id": "fund-1", "display_name": "Agent · Bookkeeping bot", "last4": "4242", "state": "ACTIVE"}
    method, path, params, body, idem = calls[0]
    assert (method, path, idem) == ("POST", "/developer/v1/funds", True)
    assert body == {"user_id": "user-1", "display_name": "Agent · Bookkeeping bot",
                    "spending_restrictions": {"limit": {"amount": 50000, "currency_code": "USD"}, "interval": "MONTHLY"},
                    "permitted_spend_types": {"virtual_card": True, "physical_card": False, "reimbursements": False}}
    assert rc.fund_of(conn, "mailbox bot@x.com")["card_last4"] == "4242"
    row = conn.execute("SELECT agent, source, attribution, target FROM actions").fetchone()
    assert row[:3] == (RECEIPT_AGENT, "input", "human")
    assert row[3] == "Gave Bookkeeping bot a Ramp card: 500.00 USD per monthly, card •••• 4242 (sandbox)"

    for bad in ({"interval": "hourly"}, {"limit": 0}):
        try:
            rc.issue_fund(conn, s, "a", "A", bad.get("limit", 10), bad.get("interval", "daily"), "marc", call=call)
            assert False, "should have refused"
        except rc.RampError:
            pass
    try:
        rc.issue_fund(conn, ramp_settings({**ENV, "RECEIPT_RAMP_USER_ID": ""}), "a", "A", 10, "daily", "marc", call=call)
        assert False
    except rc.RampError as exc:
        assert "RECEIPT_RAMP_USER_ID" in str(exc)
    conn.close()


def test_sync_attributes_fund_transactions_by_construction(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO ramp_funds (fund_id, agent, display_name, limit_amount, currency, interval, card_last4, state, created_at, by_user) "
                 "VALUES ('fund-1', 'mailbox bot@x.com', 'Agent · Bookkeeping bot', 500, 'USD', 'MONTHLY', '4242', 'ACTIVE', 1, 'marc')")
    conn.commit()
    txs = [
        {"id": "tx-1", "amount": 12.5, "currency_code": "USD", "merchant_name": "ACME CLOUD", "fund_id": "fund-1", "card_id": "card-1",
         "state": "CLEARED", "user_transaction_time": "2026-09-15T10:00:00Z", "merchant_category_code": "7372"},
        {"id": "tx-2", "entity_amount": {"amount": 9900, "currency_code": "USD", "minor_unit_conversion_rate": 100}, "merchant_name": "BIG SPEND",
         "fund_id": "fund-1", "state": "DECLINED", "decline_details": {"reason": "AUTHORIZER_CARD_LIMIT"}, "user_transaction_time": "2026-09-15T11:00:00Z"},
        {"id": "tx-3", "amount": 40, "currency_code": "USD", "merchant_name": "LUNCH", "fund_id": "fund-other", "state": "PENDING",
         "card_holder": {"first_name": "Marc", "last_name": "B"}, "user_transaction_time": "2026-09-15T12:00:00Z"},
    ]
    calls = []
    funds_page = {"data": [{"id": "fund-1", "state": "SUSPENDED", "cards": [{"last_four": "4242"}]}], "page": {"next": None}}
    call = _fake({"GET /developer/v1/transactions": {"data": txs, "page": {"next": None}},
                  "GET /developer/v1/funds": funds_page}, calls)
    s = ramp_settings(ENV)
    counts = rc.sync(conn, s, "marc", call=call)
    assert counts == {"seen": 3, "new": 3, "agent": 2, "declined": 1, "funds": 1}
    assert calls[0][2]["order_by_date_asc"] == "true" and "from_date" in calls[0][2]
    correlate(conn)
    rows = {r[0]: r for r in conn.execute(
        "SELECT source_ref, agent, source, attribution, action_type, target, amount, currency, reversible, confidence_note, artifact_link FROM actions")}
    a = rows["ramp:tx-1"]
    assert a[1:5] == ("mailbox bot@x.com", "log", "agent", "purchase") and a[5:8] == ("ACME CLOUD", 12.5, "USD") and a[8] == 0
    assert "by credential: Ramp fund 'Agent · Bookkeeping bot'" in a[9] and a[10] == "https://demo.ramp.com/transactions/tx-1"
    d = rows["ramp:tx-2"]
    assert d[5] == "DECLINED: BIG SPEND" and d[6] == 99.0 and d[8] == 1 and "declined by Ramp: AUTHORIZER_CARD_LIMIT" in d[9]
    o = rows["ramp:tx-3"]
    assert o[1:4] == ("ramp card (sandbox)", "card", "human") and "by credential: Ramp card of Marc B" in o[9]
    assert "attributed by the credential's owner" in o[9]                     # the correlator left it alone
    assert float(conn.execute("SELECT value FROM meta WHERE key = 'ramp_since'").fetchone()[0]) > 1.7e9   # sync time, not tx time
    assert conn.execute("SELECT state FROM ramp_funds WHERE fund_id = 'fund-1'").fetchone()[0] == "SUSPENDED"   # refreshed
    assert conn.execute("SELECT COUNT(*) FROM coverage WHERE source = 'card'").fetchone()[0] == 1

    # Second sync: same rows are not duplicated; a state change is noted in raw_json.
    txs[2]["state"] = "CLEARED"
    counts = rc.sync(conn, s, "marc", call=call)
    assert counts["new"] == 0 and conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 3
    assert json.loads(conn.execute("SELECT raw_json FROM actions WHERE source_ref = 'ramp:tx-3'").fetchone()[0])["state"] == "CLEARED"
    assert "csecret" not in json.dumps(conn.execute("SELECT raw_json, confidence_note FROM actions").fetchall())
    conn.close()


def test_stop_controls_and_agents_page_for_a_fund(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    conn.execute("INSERT INTO ramp_funds (fund_id, agent, display_name, limit_amount, currency, interval, card_last4, state, created_at, by_user) "
                 "VALUES ('fund-1', 'mailbox bot@x.com', 'Agent · Bookkeeping bot', 500, 'USD', 'MONTHLY', '4242', 'ACTIVE', 1, 'marc')")
    conn.execute("INSERT INTO actions (timestamp, agent, user, source, action_type, target, attribution, confidence_note, raw_json) "
                 "VALUES (1, 'mailbox bot@x.com', 'marc', 'log', 'purchase', 'x', 'agent', 'n', '{}')")
    conn.commit()
    ids = [c["id"] for c in controls(ENV, conn)]
    assert "ramp_suspend:fund-1" in ids and "ramp_terminate:fund-1" in ids and "ramp_unsuspend:fund-1" not in ids
    assert [c["id"] for c in controls(ENV)][:1] == ["codex_processes"]                     # no conn: no fund controls

    calls = []
    call = _fake({"POST /developer/v1/funds/fund-1/suspension": {"id": "fund-1", "state": "SUSPENDED"},
                  "DELETE /developer/v1/funds/fund-1/suspension": {"id": "fund-1", "state": "ACTIVE"},
                  "DELETE /developer/v1/funds/fund-1": {"id": "fund-1", "state": "TERMINATED"}}, calls)
    r = stop_run(conn, "ramp_suspend:fund-1", "marc", ENV, ramp_call=call, end_processes=lambda n: 0)
    assert r["ok"] and r["message"] == "suspended the Ramp fund — Ramp now reports it suspended"
    assert conn.execute("SELECT state FROM ramp_funds").fetchone()[0] == "SUSPENDED"
    assert "ramp_unsuspend:fund-1" in [c["id"] for c in controls(ENV, conn)]
    r = stop_run(conn, "ramp_unsuspend:fund-1", "marc", ENV, ramp_call=call, end_processes=lambda n: 0)
    assert r["ok"] and conn.execute("SELECT state FROM ramp_funds").fetchone()[0] == "ACTIVE"
    r = stop_run(conn, "all", "marc", ENV, ramp_call=call, end_processes=lambda n: 0)      # Freeze all suspends, never terminates
    assert r["ok"] and calls[-1][1].endswith("/suspension") and conn.execute("SELECT state FROM ramp_funds").fetchone()[0] == "SUSPENDED"
    r = stop_run(conn, "ramp_terminate:fund-1", "marc", ENV, ramp_call=call, end_processes=lambda n: 0)
    assert r["ok"] and conn.execute("SELECT state FROM ramp_funds").fetchone()[0] == "TERMINATED"
    assert not [c for c in controls(ENV, conn) if c["id"].startswith("ramp_")]
    presses = conn.execute("SELECT target FROM actions WHERE agent = ? ORDER BY id", (RECEIPT_AGENT,)).fetchall()
    assert presses[0][0].startswith("Suspend the agent's Ramp card: suspended") and presses[-1][0].startswith("Terminate")

    def refuse(settings, method, path, params=None, body=None, idempotent=False):
        raise rc.RampError(403, {"error_v2": {"message": "insufficient scope"}})
    conn.execute("UPDATE ramp_funds SET state = 'ACTIVE'"); conn.commit()
    r = stop_run(conn, "ramp_suspend:fund-1", "marc", ENV, ramp_call=refuse, end_processes=lambda n: 0)
    assert not r["ok"] and r["message"] == "Ramp refused: 403: insufficient scope"
    conn.close()

    agents = list_agents(connect(db), ENV)
    a = next(x for x in agents if x["name"] == "mailbox bot@x.com")
    assert a["fund"]["last4"] == "4242" and a["fund"]["interval"] == "monthly"
    assert "ramp card (sandbox)" in [x["name"] for x in agents]


def test_agents_card_route(tmp_path, monkeypatch):
    import statement as statement_module
    db = tmp_path / "t.db"
    conn = connect(db)
    conn.execute("INSERT INTO actions (timestamp, agent, user, source, action_type, target, attribution, confidence_note, raw_json) "
                 "VALUES (1, 'claude-code', 'marc', 'log', 'file_write', 'x', 'agent', 'n', '{}')")
    conn.commit(); conn.close()
    monkeypatch.setattr(statement_module, "ramp_settings", lambda: ramp_settings(ENV))
    issued = []
    monkeypatch.setattr(statement_module, "issue_fund",
                        lambda conn, s, agent, label, limit, interval, user: issued.append((agent, label, limit, interval)) or
                        {"fund_id": "f", "display_name": "d", "last4": "1111", "state": "ACTIVE"})
    client = create_app(db).test_client()
    resp = client.post("/agents/card", data={"agent": "claude-code", "limit": "250", "interval": "daily"})
    assert resp.status_code == 302 and issued == [("claude-code", "Claude Code", 250.0, "DAILY")]
    assert "Gave+Claude+Code+a+Ramp+card%3A+250.00+USD+per+daily%2C+card+%E2%80%A2%E2%80%A2%E2%80%A2%E2%80%A2+1111" in resp.headers["Location"]
    html = client.get("/agents?msg=hello&ok=1").get_data(as_text=True)
    assert "<b>Done.</b> hello" in html and 'name="interval"' in html and "Give a card" in html
    monkeypatch.setattr(statement_module, "issue_fund", lambda *a: (_ for _ in ()).throw(rc.RampError(400, {"error": "nope"})))
    resp = client.post("/agents/card", data={"agent": "claude-code", "limit": "abc"})
    assert "Ramp+did+not+issue+the+card" in resp.headers["Location"] and "ok=0" in resp.headers["Location"]

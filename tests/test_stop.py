import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agents import RECEIPT_AGENT  # noqa: E402
from correlator import correlate  # noqa: E402
from statement import create_app  # noqa: E402
from stop import controls, find, presses, run  # noqa: E402
from store import connect  # noqa: E402
from stripe_connector import StripeError  # noqa: E402

ENV = {"RECEIPT_IMAP_USER": "bot@gmail.com", "RECEIPT_IMAP_PASSWORD": "pw-secret-zz", "RECEIPT_EMAIL_AGENT": "Bookkeeping mailbox",
       "RECEIPT_STRIPE_TEST_KEY": "sk_test_secret_zz", "RECEIPT_STRIPE_AGENT": "Bookkeeping card",
       "RECEIPT_GOOGLE_CLIENT_ID": "id", "RECEIPT_GOOGLE_CLIENT_SECRET": "gsecretzz",
       "RECEIPT_GOOGLE_AGENT_EMAILS": "bot@gmail.com", "RECEIPT_CALENDAR": "on"}


def test_controls_cover_the_configuration_without_secrets():
    cs = controls(ENV)
    ids = [c["id"] for c in cs]
    assert ids == ["mailbox_signout", "mailbox_suspend", "stripe_freeze", "stripe_cancel", "stripe_key",
                   "google_signout:bot@gmail.com", "google_suspend:bot@gmail.com", "google_token", "mac_calendar",
                   "codex_processes", "claude_processes", "cowork_schedule"]
    dumped = json.dumps(cs)
    assert "pw-secret" not in dumped and "secret_zz" not in dumped and "gsecretzz" not in dumped
    by = {c["id"]: c for c in cs}
    assert by["mailbox_signout"]["mode"] == "link" and by["mailbox_signout"]["url"].startswith("https://myaccount.google.com")
    assert by["mailbox_signout"]["agent"] == "Bookkeeping mailbox" and by["stripe_freeze"]["agent"] == "Bookkeeping card"
    assert by["stripe_cancel"]["danger"] and by["stripe_cancel"]["reversible"] == 0
    assert by["stripe_key"]["url"] == "https://dashboard.stripe.com/test/apikeys"
    assert by["google_token"]["effect"] == "blinds the receipt" and by["mac_calendar"]["effect"] == "blinds the receipt"
    assert by["cowork_schedule"]["url"] is None and len(by["cowork_schedule"]["steps"]) == 3
    # Nothing configured: only the local-process and Cowork controls remain.
    assert [c["id"] for c in controls({})] == ["codex_processes", "claude_processes", "cowork_schedule"]
    assert find("nope", ENV) is None


def _fake_stripe(cards, calls):
    def api(key, method, path, params=None):
        calls.append((method, path, params))
        if method == "GET":
            return {"data": cards}
        return {"id": path.rsplit("/", 1)[-1], "status": params["status"]}
    return api


def test_freeze_cancel_and_failures_are_recorded(tmp_path):
    conn = connect(tmp_path / "t.db")
    calls = []
    r = run(conn, "stripe_freeze", "marc", ENV, stripe_api=_fake_stripe([{"id": "ic_1", "last4": "4242"}, {"id": "ic_2", "last4": "1111"}], calls))
    assert r["ok"] and r["message"] == "froze 2 cards (•••• 4242, •••• 1111)"
    assert calls[0] == ("GET", "/v1/issuing/cards", {"status": "active", "limit": 100})
    assert calls[1:] == [("POST", "/v1/issuing/cards/ic_1", {"status": "inactive"}), ("POST", "/v1/issuing/cards/ic_2", {"status": "inactive"})]

    calls.clear()
    r = run(conn, "stripe_cancel", "marc", ENV, stripe_api=_fake_stripe([{"id": "ic_1", "last4": "4242"}], calls))
    assert r["ok"] and r["message"].startswith("cancelled 1 card") and calls[1][2] == {"status": "canceled"}

    r = run(conn, "stripe_freeze", "marc", ENV, stripe_api=_fake_stripe([], []))
    assert r["ok"] and "nothing to freeze" in r["message"]

    def refuse(key, method, path, params=None):
        raise StripeError(403, {"error": {"message": "Issuing is not enabled"}})
    r = run(conn, "stripe_freeze", "marc", ENV, stripe_api=refuse)
    assert not r["ok"] and r["message"].startswith("Stripe refused")

    correlate(conn)
    rows = conn.execute("SELECT agent, source, attribution, reversible, target, confidence_note FROM actions ORDER BY id").fetchall()
    assert len(rows) == 4 and all(r[:3] == (RECEIPT_AGENT, "input", "human") for r in rows)
    assert rows[0][3] == 1 and rows[0][4].startswith("Freeze the agent's Issuing cards: froze 2 cards")
    assert rows[1][3] == 0 and "gone for good" in rows[1][5]
    assert "it did not work" in rows[3][5]
    hist = presses(conn)
    assert [h["ok"] for h in hist] == [False, True, True, True] and hist[0]["agent"] == "Bookkeeping card"
    conn.close()


def test_google_revoke_and_processes(tmp_path):
    conn = connect(tmp_path / "t.db")
    token = tmp_path / "google_token.json"
    token.write_text(json.dumps({"refresh_token": "rt-secret", "access_token": "at"}))
    posted = []
    r = run(conn, "google_token", "marc", ENV, post=lambda url, data: posted.append((url, data)) or {}, token_path=token)
    assert r["ok"] and posted == [("https://oauth2.googleapis.com/revoke", {"token": "rt-secret"})] and not token.exists()
    r = run(conn, "google_token", "marc", ENV, post=lambda *a: posted.append(a), token_path=token)
    assert r["ok"] and "no Google token" in r["message"] and len(posted) == 1
    assert "rt-secret" not in json.dumps(conn.execute("SELECT target, raw_json FROM actions").fetchall())

    ended = []
    r = run(conn, "codex_processes", "marc", ENV, end_processes=lambda name: ended.append(name) or 3)
    assert r["ok"] and r["message"] == "ended 3 Codex processes" and ended == ["codex"]
    r = run(conn, "claude_processes", "marc", ENV, end_processes=lambda name: 0)
    assert r["ok"] and r["message"] == "no Claude Code session was running"

    r = run(conn, "mailbox_signout", "marc", ENV)
    assert r["ok"] and "opened the steps" in r["message"]
    assert conn.execute("SELECT target FROM actions ORDER BY id DESC LIMIT 1").fetchone()[0].startswith("Opened the steps for: Sign the mailbox out")
    assert run(conn, "nope", "marc", ENV) == {"ok": False, "message": "no such control", "results": []}
    conn.close()


def test_freeze_all_skips_permanent_and_links(tmp_path):
    conn = connect(tmp_path / "t.db")
    token = tmp_path / "google_token.json"
    token.write_text(json.dumps({"refresh_token": "rt"}))
    calls, ended = [], []
    r = run(conn, "all", "marc", ENV, stripe_api=_fake_stripe([{"id": "ic_1", "last4": "4242"}], calls),
            post=lambda url, data: {}, token_path=token, end_processes=lambda name: ended.append(name) or 1)
    assert r["ok"] and len(r["results"]) == 4                                   # freeze, token, codex, claude
    assert not any(p == {"status": "canceled"} for _, _, p in calls)             # cancel skipped
    assert ended == ["codex", "claude"] and not token.exists()
    last = conn.execute("SELECT target FROM actions ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert last.startswith("Freeze all: froze 1 card")
    assert run(conn, "all", "marc", {}, end_processes=lambda name: 0)["ok"]     # nothing configured: processes only
    conn.close()


def test_stop_page_and_route(tmp_path, monkeypatch):
    import statement as statement_module
    db = tmp_path / "t.db"
    connect(db).close()
    monkeypatch.setattr(statement_module, "stop_controls", lambda *a, **k: controls(ENV))
    client = create_app(db).test_client()
    html = client.get("/stop").get_data(as_text=True)
    assert 'href="/stop" class="on"' in html and "Freeze all" in html
    assert "Bookkeeping mailbox" in html and "Freeze the agent" in html and "Cancel cards" in html
    assert "stops the agent" in html and "blinds the receipt" in html and "No Stop button has been pressed yet" in html
    assert "pw-secret" not in html and "secret_zz" not in html

    monkeypatch.setattr(statement_module, "stop_run_control",
                        lambda conn, cid, user: {"ok": cid == "codex_processes", "message": f"ran {cid}", "results": []})
    resp = client.post("/stop/run", data={"id": "codex_processes"})
    assert resp.status_code == 302 and "msg=ran+codex_processes" in resp.headers["Location"] and "ok=1" in resp.headers["Location"]
    html = client.get("/stop?msg=ran+codex_processes&ok=1").get_data(as_text=True)
    assert "<b>Done.</b> ran codex_processes" in html
    html = client.get("/stop?msg=x&ok=0").get_data(as_text=True)
    assert "<b>Not done.</b>" in html

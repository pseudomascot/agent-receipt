import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import settings as sp  # noqa: E402
from agents import RECEIPT_AGENT  # noqa: E402
from config import load_env  # noqa: E402
from statement import create_app  # noqa: E402
from store import connect  # noqa: E402


def test_write_env_keeps_comments_updates_and_appends(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# header\nRECEIPT_IMAP_USER=old@x.com\n# RECEIPT_IMAP_HOST=commented\nOTHER=1\n")
    sp.write_env({"RECEIPT_IMAP_USER": "new@x.com", "RECEIPT_IMAP_PASSWORD": "pw zz", "OTHER": None}, env)
    text = env.read_text()
    assert text.splitlines() == ["# header", "RECEIPT_IMAP_USER=new@x.com", "# RECEIPT_IMAP_HOST=commented", "",
                                 "# --- set from the Settings page ---", "RECEIPT_IMAP_PASSWORD=pw zz"]
    assert oct(env.stat().st_mode)[-3:] == "600"
    assert load_env(env) == {"RECEIPT_IMAP_USER": "new@x.com", "RECEIPT_IMAP_PASSWORD": "pw zz"} or load_env(env)["RECEIPT_IMAP_PASSWORD"] == "pw zz"


def test_view_masks_secrets_and_ignores_placeholders(tmp_path):
    env = tmp_path / ".env"
    env.write_text("RECEIPT_IMAP_USER=bot@x.com\nRECEIPT_IMAP_PASSWORD=hunter2secret\nRECEIPT_STRIPE_TEST_KEY=sk_test_...\nRECEIPT_RAMP_CLIENT_ID=PASTE-CLIENT-ID\n")
    v = sp.view(env)
    by = {s["id"]: s for s in v["sections"]}
    pw = next(f for f in by["mailbox"]["fields"] if f["key"] == "RECEIPT_IMAP_PASSWORD")
    assert pw["value"] == "" and pw["saved"] is True and by["mailbox"]["configured"] is True
    assert "hunter2secret" not in json.dumps(v)
    stripe_key = next(f for f in by["stripe"]["fields"] if f["key"] == "RECEIPT_STRIPE_TEST_KEY")
    assert stripe_key["saved"] is False and by["stripe"]["configured"] is False          # placeholder = not set
    assert next(f for f in by["ramp"]["fields"] if f["key"] == "RECEIPT_RAMP_CLIENT_ID")["value"] == ""


def test_save_records_field_names_never_values(tmp_path):
    env = tmp_path / ".env"; env.write_text("")
    conn = connect(tmp_path / "t.db")
    changed = sp.save("mailbox", {"RECEIPT_IMAP_USER": ["bot@x.com"], "RECEIPT_IMAP_PASSWORD": ["pw-secret-zz"],
                                  "RECEIPT_IMAP_HOST": ["imap.gmail.com"], "RECEIPT_EMAIL_AGENT": [""]}, "marc", conn, env)
    assert changed == ["RECEIPT_IMAP_USER", "RECEIPT_IMAP_PASSWORD", "RECEIPT_IMAP_HOST"]
    assert load_env(env)["RECEIPT_IMAP_PASSWORD"] == "pw-secret-zz"
    row = conn.execute("SELECT agent, source, attribution, target, raw_json FROM actions").fetchone()
    assert row[:3] == (RECEIPT_AGENT, "input", "human")
    assert row[3] == "Changed settings: Agent mailbox (RECEIPT_IMAP_USER, RECEIPT_IMAP_PASSWORD, RECEIPT_IMAP_HOST)"
    assert "pw-secret" not in row[3] + row[4]
    # Blank secret keeps it; same values = no change, no row; clear removes it.
    assert sp.save("mailbox", {"RECEIPT_IMAP_USER": ["bot@x.com"], "RECEIPT_IMAP_PASSWORD": [""], "RECEIPT_IMAP_HOST": ["imap.gmail.com"]}, "marc", conn, env) == []
    assert load_env(env)["RECEIPT_IMAP_PASSWORD"] == "pw-secret-zz"
    assert sp.save("mailbox", {"RECEIPT_IMAP_USER": ["bot@x.com"], "RECEIPT_IMAP_HOST": ["imap.gmail.com"], "clear_RECEIPT_IMAP_PASSWORD": ["1"]}, "marc", conn, env) == ["RECEIPT_IMAP_PASSWORD"]
    assert "RECEIPT_IMAP_PASSWORD" not in load_env(env)
    assert sp.save("calendar", {"RECEIPT_CALENDAR": ["on"]}, "marc", conn, env) == ["RECEIPT_CALENDAR"] and load_env(env)["RECEIPT_CALENDAR"] == "on"
    assert sp.save("mailbox", {}, "marc", conn, env) == [] and load_env(env)["RECEIPT_IMAP_USER"] == "bot@x.com"   # partial form touches nothing
    assert sp.save("nope", {}, "marc", conn, env) == []
    conn.close()


def test_tests_report_plainly_without_network(tmp_path, monkeypatch):
    assert sp.test_mailbox({}) == (False, "address and app password are not both saved yet")
    assert sp.test_stripe({})[0] is False and sp.test_ramp({})[0] is False and sp.test_google({})[0] is False
    assert sp.test_calendar({}) == (False, "switched off")
    import stripe_connector
    monkeypatch.setattr(stripe_connector, "request", lambda key, m, p, params=None: {"data": [1, 2]})
    assert sp.test_stripe({"RECEIPT_STRIPE_TEST_KEY": "sk_test_zz"}) == (True, "connected (test mode): 2 recent charge(s) visible")
    import ramp_connector
    monkeypatch.setattr(ramp_connector, "list_users", lambda s: [{"id": "u1", "name": "Pam Beesly", "email": "p@x", "role": "BUSINESS_USER", "status": "USER_ACTIVE"}])
    monkeypatch.setattr(ramp_connector, "list_all", lambda s, path, params=None, call=None: [{}, {}, {}])
    env = {"RECEIPT_RAMP_CLIENT_ID": "cid", "RECEIPT_RAMP_CLIENT_SECRET": "cs", "RECEIPT_RAMP_USER_ID": "u1"}
    assert sp.test_ramp(env) == (True, "connected (sandbox): 1 user(s), 3 fund(s); card holder: Pam Beesly")
    assert sp.test_ramp({**env, "RECEIPT_RAMP_USER_ID": ""})[1].endswith("pick a card holder below")
    p = tmp_path / ".env"; p.write_text("RECEIPT_RAMP_CLIENT_ID=cid\nRECEIPT_RAMP_CLIENT_SECRET=cs\n")
    assert [u["name"] for u in sp.ramp_user_choices(p)] == ["Pam Beesly"]
    assert sp.ramp_user_choices(tmp_path / "none.env") == []


def test_settings_page_and_routes(tmp_path, monkeypatch):
    import statement as statement_module
    env = tmp_path / ".env"; env.write_text("")
    monkeypatch.setattr(sp, "ENV_PATH", env)
    monkeypatch.setattr(sp, "GOOGLE_TOKEN_PATH", tmp_path / "no-token.json")
    monkeypatch.setattr(sp, "ramp_user_choices", lambda path=None: [])
    db = tmp_path / "t.db"; connect(db).close()
    client = create_app(db).test_client()
    html = client.get("/settings").get_data(as_text=True)
    assert 'href="/settings" class="on"' in html and "Agent mailbox" in html and "Sign in as the agent" in html
    assert html.count("not set up") >= 5 and 'type="password"' in html
    resp = client.post("/settings/save/stripe", data={"RECEIPT_STRIPE_TEST_KEY": "sk_test_zzsecret", "RECEIPT_STRIPE_AGENT": "Agent card"})
    assert resp.status_code == 302 and "saved+RECEIPT_STRIPE_TEST_KEY%2C+RECEIPT_STRIPE_AGENT" in resp.headers["Location"]
    html = client.get("/settings").get_data(as_text=True)
    assert "zzsecret" not in html and "leave blank to keep" in html and 'value="Agent card"' in html
    assert load_env(env)["RECEIPT_STRIPE_TEST_KEY"] == "sk_test_zzsecret"
    monkeypatch.setattr(sp, "run_test", lambda section_id, path=None: (False, "Stripe refused: 401"))
    resp = client.post("/settings/test/stripe", data={"RECEIPT_STRIPE_AGENT": "Agent card"})
    assert "Stripe%3A+Stripe+refused%3A+401" in resp.headers["Location"] and "ok=0" in resp.headers["Location"]
    started = []
    monkeypatch.setattr(sp, "start_google_signin", lambda root: started.append(root) or "opening")
    resp = client.post("/settings/google-signin", data={"RECEIPT_GOOGLE_CLIENT_ID": "id", "RECEIPT_GOOGLE_CLIENT_SECRET": "s"})
    assert resp.status_code == 302 and started and "opening" in resp.headers["Location"]
    rows = connect(db).execute("SELECT target FROM actions WHERE agent = ? ORDER BY id", (RECEIPT_AGENT,)).fetchall()
    assert rows[0][0].startswith("Changed settings: Stripe (RECEIPT_STRIPE_TEST_KEY") and "zzsecret" not in json.dumps(rows)

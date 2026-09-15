import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agents import RECEIPT_AGENT, history, kind_of, known_identities, list_agents, retire, restore, retired  # noqa: E402
from alerts import evaluate, unseen  # noqa: E402
from correlator import correlate  # noqa: E402
from statement import create_app  # noqa: E402
from store import connect  # noqa: E402

T = datetime(2026, 9, 14, 10, 30).timestamp()
ENV = {"RECEIPT_IMAP_USER": "bot@example.com", "RECEIPT_IMAP_PASSWORD": "hunter2secret",
       "RECEIPT_STRIPE_TEST_KEY": "sk_test_zzsecret", "RECEIPT_GOOGLE_CLIENT_ID": "id", "RECEIPT_GOOGLE_CLIENT_SECRET": "gsecretzz",
       "RECEIPT_GOOGLE_AGENT_EMAILS": "bot@example.com"}


def _seed(db_path):
    conn = connect(db_path)
    conn.executemany(
        "INSERT INTO actions (timestamp, agent, user, source, action_type, target, reversible, amount, attribution, confidence_note, raw_json) "
        "VALUES (?, ?, ?, 'log', ?, ?, ?, ?, 'agent', 'agent log', '{}')",
        [
            (T, "claude-code", "marc", "file_write", "a.py", 1, None),
            (T + 60, "claude-code", "marc", "execute", "rm x", 0, None),
            (T + 120, "cowork (scheduled task)", "marc", "execute", "deploy", 0, None),
            (T + 180, "mailbox bot@example.com", "sam", "purchase", "ACME", 0, 12.0),
        ],
    )
    conn.commit()
    return conn


def test_known_identities_and_kinds():
    known = known_identities(ENV)
    assert known["mailbox bot@example.com"][0] == "mailbox"
    assert known["stripe card (test mode)"] == ("card", "a Stripe account (test mode)")
    assert known["google calendar (bot@example.com)"][0] == "google account"
    assert "secret" not in str(known)                                              # no secrets in labels
    assert kind_of("claude-code", known)[0] == "local agent"
    assert "schedule" in kind_of("cowork (scheduled task)", known)[1]
    assert kind_of("claude-code / worker-3", known)[0] == "sub-agent"
    assert kind_of("my-bot", known)[0] == "declared"


def test_list_retire_restore_and_alert(tmp_path):
    conn = _seed(tmp_path / "t.db")
    agents = list_agents(conn, ENV)
    names = [a["name"] for a in agents]
    assert names[:3] == ["mailbox bot@example.com", "cowork (scheduled task)", "claude-code"]   # newest first
    assert "stripe card (test mode)" in names and "google calendar (bot@example.com)" in names  # configured, quiet
    cc = next(a for a in agents if a["name"] == "claude-code")
    assert cc["total"] == 2 and cc["irreversible"] == 1 and cc["users"] == ["marc"]
    assert cc["by_type"] == {"file_write": 1, "execute": 1} and cc["retired"] is None
    mail = next(a for a in agents if a["name"] == "mailbox bot@example.com")
    assert mail["money"] == 1 and mail["kind"] == "mailbox"

    row_id = retire(conn, "claude-code", "marc", "kept deleting things")
    assert row_id and retire(conn, "claude-code", "marc") is None                # second press is a no-op
    assert "claude-code" in retired(conn) and retired(conn)["claude-code"]["note"] == "kept deleting things"
    assert conn.execute("SELECT COUNT(*) FROM actions WHERE agent = 'claude-code'").fetchone()[0] == 2   # history untouched

    correlate(conn)                                                              # the button press stays a person's
    rec = conn.execute("SELECT agent, source, attribution, target FROM actions WHERE id = ?", (row_id,)).fetchone()
    assert rec == (RECEIPT_AGENT, "input", "human", "Retired agent: claude-code — kept deleting things")
    assert history(conn)[0]["event"] == "retire" and history(conn)[0]["agent"] == "claude-code"

    agents = list_agents(conn, ENV)
    assert agents[-1]["name"] == "claude-code" and agents[-1]["retired"]["by_user"] == "marc"   # retired sort last
    assert RECEIPT_AGENT not in [a["name"] for a in agents]

    # An old action (before retirement) does not alert; a new one does.
    evaluate(conn)
    conn.execute("INSERT INTO actions (timestamp, agent, user, source, action_type, target, reversible, attribution, confidence_note, raw_json) "
                 "VALUES (?, 'claude-code', 'marc', 'log', 'file_write', 'b.py', 1, 'agent', 'agent log', '{}')", (time.time() + 1,))
    conn.execute("INSERT INTO actions (timestamp, agent, user, source, action_type, target, reversible, attribution, confidence_note, raw_json) "
                 "VALUES (?, 'claude-code', 'marc', 'log', 'file_write', 'old.py', 1, 'agent', 'agent log', '{}')", (T - 5,))
    conn.commit()
    new = evaluate(conn)
    rules = [(n["rule"], n["target"]) for n in new]
    assert ("retired_agent_acted", "b.py") in rules and ("retired_agent_acted", "old.py") not in rules
    assert any(i["rule"] == "retired_agent_acted" for i in unseen(conn))

    assert restore(conn, "claude-code", "marc") and restore(conn, "claude-code", "marc") is None
    assert "claude-code" not in retired(conn)
    assert [h["event"] for h in history(conn)] == ["restore", "retire"]
    conn.close()


def test_agents_page_and_form(tmp_path):
    db = tmp_path / "t.db"
    _seed(db).close()
    client = create_app(db).test_client()
    html = client.get("/agents").get_data(as_text=True)
    assert "claude-code" in html and "cowork (scheduled task)" in html and 'href="/agents" class="on"' in html
    assert "Nothing retired or restored" in html and 'name="action" value="retire"' in html

    resp = client.post("/agents/status", data={"agent": "claude-code", "action": "retire", "note": "noisy"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/agents")
    html = client.get("/agents").get_data(as_text=True)
    assert 'class="retired"' in html and "Retired agent: claude-code — noisy" in html and "Restore" in html
    assert "Claude Code · retired" in client.get("/day/2026-09-14").get_data(as_text=True)   # chip label
    day = client.get(f"/day/{datetime.now().date().isoformat()}").get_data(as_text=True)
    assert "Retired agent: claude-code" in day                                   # the press is on the statement

    client.post("/agents/status", data={"agent": "claude-code", "action": "restore"})
    html = client.get("/agents").get_data(as_text=True)
    assert 'class="retired"' not in html and "Restored agent: claude-code" in html
    client.post("/agents/status", data={"agent": "", "action": "retire"})      # ignored
    client.post("/agents/status", data={"agent": "claude-code", "action": "bogus"})
    assert not retired(connect(db))


def test_display_names():
    from agents import display
    known = known_identities(ENV)
    assert display("claude-code (claude-desktop)", {}, known) == {
        "name": "Claude Code", "sub": "in the Claude app", "kind": "local agent", "raw": "claude-code (claude-desktop)", "nickname": ""}
    assert display("claude-code (cli)", {}, known)["sub"] == "in the Terminal"
    d = display("claude-code (claude-desktop) / general-purpose: Write-one-file test sub-agent", {}, known)
    assert d["name"] == "Sub-agent of Claude Code" and d["sub"] == "Write-one-file test sub-agent · in the Claude app" and d["kind"] == "sub-agent"
    assert display("cowork (scheduled task)", {}, known)["name"] == "Cowork · scheduled task"
    assert display("cowork", {}, known) == {"name": "Cowork", "sub": "in the Claude app", "kind": "local agent", "raw": "cowork", "nickname": ""}
    assert display("codex (codex_work_desktop)", {}, known)["name"] == "Codex (OpenAI)"
    assert display("google calendar (bot@example.com)", {}, known) == {
        "name": "Google account", "sub": "bot@example.com", "kind": "google account", "raw": "google calendar (bot@example.com)", "nickname": ""}
    assert display("mailbox bot@example.com", {}, known)["sub"] == "its own mailbox, bot@example.com"
    assert display(RECEIPT_AGENT, {}, known)["name"] == "Agent Receipt"
    assert display("my-bot", {}, known) == {"name": "my-bot", "sub": "declares its own actions through the receipt-line inbox", "kind": "declared", "raw": "my-bot", "nickname": ""}
    nick = display("mailbox bot@example.com", {"mailbox bot@example.com": "Bookkeeping bot"}, known)
    assert nick["name"] == "Bookkeeping bot" and nick["sub"] == "its own mailbox, bot@example.com" and nick["nickname"] == "Bookkeeping bot"
    nick = display("claude-code (claude-desktop)", {"claude-code (claude-desktop)": "Coder"}, known)
    assert nick["name"] == "Coder" and nick["sub"] == "Claude Code"


def test_nickname_flows_everywhere(tmp_path):
    from agents import set_nickname
    db = tmp_path / "t.db"
    conn = _seed(db)
    assert set_nickname(conn, "mailbox bot@example.com", "  Bookkeeping   bot ", "marc")
    assert set_nickname(conn, "mailbox bot@example.com", "Bookkeeping bot", "marc") is None      # unchanged: no row
    assert history(conn)[0]["event"] == "name" and "Bookkeeping bot" in history(conn)[0]["what"]
    conn.close()
    client = create_app(db).test_client()
    day = client.get("/day/2026-09-14").get_data(as_text=True)
    assert "<div>Bookkeeping bot</div>" in day and "Bookkeeping bot (1)" in day                   # row + chip
    assert "Agents: Cowork · scheduled task (1), Claude Code (2), Bookkeeping bot (1)." in day or "Bookkeeping bot (1)." in day
    csv_text = client.get("/export.csv?start=2026-09-14&end=2026-09-14").get_data(as_text=True)
    assert "mailbox bot@example.com,Bookkeeping bot,sam" in csv_text                              # raw + label columns
    html = client.get("/agents").get_data(as_text=True)
    assert "<b>Bookkeeping bot</b>" in html and 'value="Bookkeeping bot"' in html
    client.post("/agents/name", data={"agent": "mailbox bot@example.com", "nickname": ""})        # clear
    assert "Bookkeeping bot" not in client.get("/day/2026-09-14").get_data(as_text=True)
    assert "Cleared the name of agent: mailbox bot@example.com" in client.get("/agents").get_data(as_text=True)

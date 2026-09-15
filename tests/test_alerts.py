import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from alerts import DEFAULTS, evaluate, mark_seen, notify_new, save_settings, settings, unseen, unseen_action_ids, unseen_count  # noqa: E402
from store import connect  # noqa: E402

NOW = time.time()


def _add(conn, ts, agent, reversible=None, note="x | agent log; input monitor was not running at the time",
         amount=None, attribution="agent", target="rm -rf x"):
    cur = conn.execute(
        "INSERT INTO actions (timestamp, agent, source, action_type, target, amount, currency, reversible, "
        "attribution, confidence_note, raw_json) VALUES (?, ?, 'log', 'execute', ?, ?, 'USD', ?, ?, ?, '{\"project\": \"P\"}')",
        (ts, agent, target, amount, reversible, attribution, note),
    )
    conn.commit()
    return cur.lastrowid


def test_rules(tmp_path):
    conn = connect(tmp_path / "t.db")
    sched = _add(conn, NOW - 60, "cowork (scheduled task)", reversible=0)
    attended = _add(conn, NOW - 50, "claude-code", reversible=0, note="x | agent log wins; physical input also present (3 events in prior 30s)")
    quiet = _add(conn, NOW - 40, "claude-code", reversible=0, note="x | agent log; no physical input in prior 30s")
    harmless = _add(conn, NOW - 30, "cowork (scheduled task)", reversible=1)
    paid = _add(conn, NOW - 20, "claude-code", amount=250.0, target="shop")
    unknown = _add(conn, NOW - 10, "claude-code", attribution="unknown")

    new = evaluate(conn)
    got = {(n["action_id"], n["rule"]) for n in new}
    assert got == {(sched, "unattended_irreversible"), (quiet, "unattended_irreversible"),
                   (paid, "money_over_threshold"), (unknown, "unknown_attribution")}
    assert attended not in {a for a, _ in got} and harmless not in {a for a, _ in got}
    assert evaluate(conn) == []                                             # nothing new second time
    assert unseen_count(conn) == 4
    conn.close()


def test_burst_rule(tmp_path):
    conn = connect(tmp_path / "t.db")
    ids = [_add(conn, NOW - 3000 + i * 10, "cowork", reversible=0) for i in range(6)]
    new = evaluate(conn)
    burst = [n["action_id"] for n in new if n["rule"] == "irreversible_burst"]
    assert burst == ids[4:]                                                 # 5th and 6th within the hour
    conn.close()


def test_unseen_listing_and_mark_seen(tmp_path):
    conn = connect(tmp_path / "t.db")
    a1 = _add(conn, NOW - 60, "cowork (scheduled task)", reversible=0)
    a2 = _add(conn, NOW - 30, "cowork (scheduled task)", reversible=0)
    evaluate(conn)
    items = unseen(conn)
    assert [i["action_id"] for i in items] == [a2, a1]                      # newest first
    assert items[0]["project"] == "P" and items[0]["rule_text"].startswith("an irreversible action")
    assert unseen_action_ids(conn, NOW - 100, NOW) == {a1: ["unattended_irreversible"], a2: ["unattended_irreversible"]}
    assert mark_seen(conn, [items[0]["id"]]) == 1
    assert unseen_count(conn) == 1
    assert mark_seen(conn) == 1
    assert unseen_count(conn) == 0
    conn.close()


def test_notify_new_only_fresh_and_batched():
    sent = []
    send = lambda t, m: sent.append(m)  # noqa: E731
    old = {"timestamp": NOW - 5 * 86400, "message": "old"}
    assert notify_new([old], send) == 0 and sent == []
    fresh = [{"timestamp": NOW, "message": f"m{i}"} for i in range(3)]
    assert notify_new(fresh, send) == 3 and sent == ["m0", "m1", "m2"]
    sent.clear()
    assert notify_new(fresh + [{"timestamp": NOW, "message": "m3"}], send) == 1
    assert sent == ["4 new alerts; open the Needs review page"]


def test_rules_are_adjustable_and_recorded(tmp_path):
    from agents import RECEIPT_AGENT
    conn = connect(tmp_path / "t.db")
    assert settings(conn) == DEFAULTS and settings(conn)["money_scope"] == "agent"

    person = _add(conn, NOW - 90, "ramp card (sandbox)", amount=800.0, attribution="human", target="Air Canada")
    agent_small = _add(conn, NOW - 80, "claude-code", amount=150.0, target="shop")
    unconfirmed0 = _add(conn, NOW - 85, "mailbox bot", amount=600.0, attribution="unknown", target="mystery")
    got0 = {(n["action_id"], n["rule"]) for n in evaluate(conn)}
    assert (agent_small, "money_over_threshold") in got0 and (unconfirmed0, "money_over_threshold") in got0   # not a person: flagged
    assert (person, "money_over_threshold") not in got0                                                        # a person's own purchase: not

    assert save_settings(conn, {"money_threshold": ["250"], "money_scope": ["all"], "burst_threshold": ["3"],
                                "on.unknown_attribution": ["0", "1"], "on.unattended_irreversible": ["0"]}, "marc")["money_threshold"] == 250.0
    cfg = settings(conn)
    assert cfg["money_scope"] == "all" and cfg["burst_threshold"] == 3
    assert cfg["on.unknown_attribution"] is True and cfg["on.unattended_irreversible"] is False   # last value wins
    row = conn.execute("SELECT agent, source, attribution, target FROM actions WHERE agent = ?", (RECEIPT_AGENT,)).fetchone()
    assert row[:3] == (RECEIPT_AGENT, "input", "human")
    assert row[3] == "Changed alert rules: payment over 250.00 (anyone); 3+ irreversible actions in an hour; off: unattended irreversible; 4 of 5 rules on"
    assert save_settings(conn, {"money_threshold": ["250"]}, "marc") == cfg                       # no change: no row
    assert conn.execute("SELECT COUNT(*) FROM actions WHERE agent = ?", (RECEIPT_AGENT,)).fetchone()[0] == 1

    person2 = _add(conn, NOW - 70, "ramp card (sandbox)", amount=300.0, attribution="human", target="Aloft")
    agent_mid = _add(conn, NOW - 60, "claude-code", amount=200.0, target="shop")                    # under the new threshold
    sched = _add(conn, NOW - 50, "cowork (scheduled task)", reversible=0)                           # rule is off
    burst = [_add(conn, NOW - 40 + i, "cowork", reversible=0) for i in range(3)]                    # 3 within the hour (incl. sched? different agent)
    got = {(n["action_id"], n["rule"]) for n in evaluate(conn)}
    assert (person2, "money_over_threshold") in got and (agent_mid, "money_over_threshold") not in got
    assert (sched, "unattended_irreversible") not in got
    assert (burst[2], "irreversible_burst") in got and (burst[1], "irreversible_burst") not in got
    assert save_settings(conn, {"money_threshold": ["abc"], "money_scope": ["bogus"], "burst_threshold": ["1"]}, "marc")["burst_threshold"] == 2
    conn.close()


def test_rules_form_on_the_alerts_page(tmp_path):
    from statement import create_app
    db = tmp_path / "t.db"
    connect(db).close()
    client = create_app(db).test_client()
    html = client.get("/alerts").get_data(as_text=True)
    assert 'name="money_threshold" value="100"' in html and '<option value="agent" selected>' in html and "Save rules" in html
    resp = client.post("/alerts/rules", data={"money_threshold": "500", "money_scope": "all", "burst_threshold": "8",
                                              "on.unknown_attribution": ["0"], "on.money_over_threshold": ["0", "1"]})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/alerts?saved=1")
    html = client.get("/alerts?saved=1").get_data(as_text=True)
    assert "<b>Saved.</b>" in html and 'name="money_threshold" value="500"' in html and '<option value="all" selected>' in html
    assert 'name="burst_threshold" value="8"' in html
    assert 'name="on.unknown_attribution" value="1">' in html and 'name="on.unknown_attribution" value="1" checked' not in html
    cfg = settings(connect(db))
    assert cfg["money_threshold"] == 500.0 and cfg["money_scope"] == "all" and cfg["on.unknown_attribution"] is False

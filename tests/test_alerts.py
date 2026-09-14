import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from alerts import evaluate, mark_seen, notify_new, unseen, unseen_action_ids, unseen_count  # noqa: E402
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

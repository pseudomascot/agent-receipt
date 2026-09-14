import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from alerts import evaluate  # noqa: E402
from config import stripe_settings  # noqa: E402
from correlator import correlate  # noqa: E402
from log_parser import INBOX, ingest_all  # noqa: E402
from queries import coverage_notes  # noqa: E402
from store import connect  # noqa: E402
from stripe_connector import StripeError, _flatten, sync  # noqa: E402

NOW = int(time.time())
SETTINGS = {"key": "sk_test_x", "mode": "test", "agent": "test-agent card"}


def _auth(i, amount, merchant, created, status="approved", last4="4242"):
    return {"id": f"iauth_{i}", "created": created, "status": status, "approved": status == "approved",
            "amount": amount, "currency": "usd", "card": {"id": "ic_1", "last4": last4},
            "merchant_data": {"name": merchant, "city": "San Francisco", "category": "computer_software_stores"}}


def _charge(i, amount, created, status="succeeded", name="Jane Client"):
    return {"id": f"ch_{i}", "created": created, "status": status, "amount": amount, "currency": "usd",
            "billing_details": {"name": name}, "description": "Invoice 1042",
            "payment_method_details": {"card": {"last4": "1111"}}}


class FakeAPI:
    def __init__(self, auths, charges, issuing_enabled=True):
        self.auths, self.charges, self.issuing_enabled = auths, charges, issuing_enabled
        self.calls = []

    def __call__(self, key, method, path, params=None):
        self.calls.append((method, path, params))
        since = (params or {}).get("created", {}).get("gt", 0)
        if path == "/v1/issuing/authorizations":
            if not self.issuing_enabled:
                raise StripeError(403, {"error": {"code": "issuing_not_enabled", "message": "Issuing is not enabled"}})
            return {"data": [a for a in self.auths if a["created"] > since]}
        if path == "/v1/charges":
            return {"data": [c for c in self.charges if c["created"] > since]}
        raise AssertionError(path)


def test_sync_authorizations_and_charges(tmp_path):
    conn = connect(tmp_path / "t.db")
    api = FakeAPI(
        auths=[_auth(1, 15000, "ACME CLOUD SERVICES", NOW - 300),
               _auth(2, 999, "DECLINED SHOP", NOW - 200, status="declined")],
        charges=[_charge(1, 42000, NOW - 100), _charge(2, 500, NOW - 50, status="failed")],
    )
    assert sync(conn, SETTINGS, user="marc", api=api) == {"authorizations": 1, "charges": 1, "issuing": "on"}
    rows = conn.execute("SELECT source, action_type, target, amount, currency, reversible, source_ref, "
                        "artifact_link, confidence_note FROM actions ORDER BY id").fetchall()
    assert rows[0][:7] == ("card", "purchase", "ACME CLOUD SERVICES (San Francisco)", 150.0, "USD", 0, "iauth_1")
    assert rows[0][7] == "https://dashboard.stripe.com/test/issuing/authorizations/iauth_1"
    assert "card ending 4242" in rows[0][8]
    assert rows[1][:7] == ("card", "other", "charged Jane Client", 420.0, "USD", 1, "ch_1")
    assert "can be refunded" in rows[1][8]

    # Second sync only asks for newer events and finds none.
    assert sync(conn, SETTINGS, user="marc", api=api) == {"authorizations": 0, "charges": 0, "issuing": "on"}
    assert api.calls[-1][2]["created"]["gt"] == NOW - 50
    api.auths.append(_auth(3, 1200, "STARBUCKS", NOW - 10))
    assert sync(conn, SETTINGS, user="marc", api=api)["authorizations"] == 1

    # Attribution + alerts: nothing typed, no log → unknown; the $150 and $420 trip the money rule.
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (NOW - 600, NOW + 60))
    conn.commit()
    correlate(conn)
    assert {r[0] for r in conn.execute("SELECT attribution FROM actions")} == {"unknown"}
    rules = {(n["action_id"], n["rule"]) for n in evaluate(conn)}
    assert sum(1 for _, r in rules if r == "money_over_threshold") == 2
    conn.close()


def test_issuing_not_enabled_still_records_charges(tmp_path):
    conn = connect(tmp_path / "t.db")
    api = FakeAPI(auths=[], charges=[_charge(1, 1000, NOW - 5)], issuing_enabled=False)
    assert sync(conn, SETTINGS, api=api) == {"authorizations": 0, "charges": 1, "issuing": "off"}
    conn.close()


def test_declared_purchase_merges_with_stripe_row(tmp_path):
    conn = connect(tmp_path / "t.db")
    api = FakeAPI(auths=[_auth(7, 1234, "STARBUCKS", NOW - 30)], charges=[])
    sync(conn, SETTINGS, user="marc", api=api)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "bot.jsonl").write_text(json.dumps({
        "ts": NOW - 30, "agent": "test-agent card", "action": "purchase", "target": "STARBUCKS",
        "id": "iauth_7", "amount": 12.34, "currency": "USD", "reversible": False}) + "\n")
    from dataclasses import replace
    assert ingest_all(conn, (replace(INBOX, root=inbox),)) == 0
    row = conn.execute("SELECT source, attribution, amount, confidence_note FROM actions").fetchone()
    assert row[:3] == ("log", "agent", 12.34)
    assert "also observed via card" in row[3]
    conn.close()


def test_settings_flatten_and_coverage():
    assert stripe_settings({"RECEIPT_STRIPE_TEST_KEY": "sk_test_abc"})["mode"] == "test"
    assert stripe_settings({"RECEIPT_STRIPE_KEY": "sk_live_abc"})["agent"] == "stripe card (live mode)"
    assert stripe_settings({}) is None
    assert _flatten({"limit": 10, "created": {"gt": 5}, "merchant_data": {"name": "X"}}) == {
        "limit": 10, "created[gt]": 5, "merchant_data[name]": "X"}
    on = {n: s for n, s, _ in coverage_notes(False, True)}
    assert on["Card charges (Stripe Issuing) and charges collected via Stripe"] == "covered"
    assert on["Email sent from the agent's mailbox"] == "not covered"

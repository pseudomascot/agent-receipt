"""Stripe connector: money the agent spent on its own card, or charged to others.

Standard library only (urllib + JSON). Two feeds, polled every refresh:
- Issuing authorizations: the agent's own virtual card being used -> `purchase`
  rows (source 'card'), timestamped by Stripe's own event time.
- Charges: money the agent collected from someone else's card -> `other` rows
  with an amount, so it shows in totals and trips the money rule.

Test keys (sk_test_) hit Stripe's sandbox: real API, fake money. Position is
kept as the newest `created` seen, per mode, in `meta`. Stripe ids are the
source_ref, so a receipt line declaring the same id merges into the row.
"""

import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

from config import stripe_settings

API = "https://api.stripe.com"
PAGE = 100


class StripeError(Exception):
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload or {}
        err = self.payload.get("error") or {}
        super().__init__(f"{status}: {err.get('message') or payload}")

    @property
    def code(self):
        return (self.payload.get("error") or {}).get("code")


def request(key: str, method: str, path: str, params: dict | None = None) -> dict:
    """One Stripe API call. `params` are form-encoded; nested keys use a[b] syntax."""
    data = urllib.parse.urlencode(_flatten(params or {})).encode() if method == "POST" else None
    url = API + path
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode(_flatten(params))
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {key}", "User-Agent": "agent-receipt"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except ValueError:
            payload = {}
        raise StripeError(exc.code, payload) from None


def _flatten(params: dict, prefix: str = "") -> dict:
    flat = {}
    for k, v in params.items():
        name = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, dict):
            flat.update(_flatten(v, name))
        elif v is not None:
            flat[name] = v
    return flat


def _last_created(conn, key: str) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return int(row[0]) if row else 0


def _set_last_created(conn, key: str, value: int) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))


INSERT = """
INSERT OR IGNORE INTO actions
    (timestamp, agent, user, source, action_type, target, amount, currency, artifact_link,
     reversible, attribution, confidence_note, raw_json, source_ref)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?)
"""


def sync(conn: sqlite3.Connection, settings: dict, user: str = "", api=request) -> dict:
    """Pull new authorizations and charges. Returns counts; rows are attributed later."""
    key, mode = settings["key"], settings["mode"]
    counts = {"authorizations": 0, "charges": 0, "issuing": "on"}
    meta_key = f"stripe_last_created:{mode}"
    since = _last_created(conn, meta_key)
    newest = since

    try:
        auths = api(key, "GET", "/v1/issuing/authorizations",
                    {"limit": PAGE, "created": {"gt": since}}).get("data", [])
    except StripeError as exc:
        if exc.status in (400, 403) or exc.code in ("issuing_not_enabled", "resource_missing"):
            auths, counts["issuing"] = [], "off"
        else:
            raise
    for a in auths:
        newest = max(newest, int(a.get("created") or 0))
        if a.get("status") != "approved" and not a.get("approved"):
            continue
        merchant = a.get("merchant_data") or {}
        card = a.get("card") or {}
        last4 = card.get("last4") if isinstance(card, dict) else None
        target = merchant.get("name") or "unknown merchant"
        if merchant.get("city"):
            target += f" ({merchant['city']})"
        before = conn.total_changes
        conn.execute(INSERT, (
            float(a["created"]), settings["agent"], user, "card", "purchase", target,
            abs(a.get("amount") or 0) / 100.0, (a.get("currency") or "usd").upper(),
            f"https://dashboard.stripe.com/{'test/' if mode == 'test' else ''}issuing/authorizations/{a['id']}",
            0,
            f"Stripe Issuing authorization{' on card ending ' + last4 if last4 else ''}; "
            "reversibility: a card charge can only be disputed, not undone",
            json.dumps({"source": "stripe", "kind": "issuing_authorization", "merchant": merchant,
                        "card_last4": last4, "status": a.get("status"), "mode": mode}),
            a["id"],
        ))
        counts["authorizations"] += conn.total_changes - before

    charges = api(key, "GET", "/v1/charges", {"limit": PAGE, "created": {"gt": since}}).get("data", [])
    for c in charges:
        newest = max(newest, int(c.get("created") or 0))
        if c.get("status") != "succeeded":
            continue
        billing = c.get("billing_details") or {}
        target = billing.get("name") or billing.get("email") or c.get("description") or c.get("customer") or "a customer"
        pm = (c.get("payment_method_details") or {}).get("card") or {}
        before = conn.total_changes
        conn.execute(INSERT, (
            float(c["created"]), settings["agent"], user, "card", "other", f"charged {target}",
            (c.get("amount") or 0) / 100.0, (c.get("currency") or "usd").upper(),
            f"https://dashboard.stripe.com/{'test/' if mode == 'test' else ''}payments/{c['id']}",
            1,
            f"Stripe charge collected{' from a card ending ' + pm['last4'] if pm.get('last4') else ''}; "
            "reversibility: can be refunded",
            json.dumps({"source": "stripe", "kind": "charge", "description": c.get("description"),
                        "customer": c.get("customer"), "mode": mode}),
            c["id"],
        ))
        counts["charges"] += conn.total_changes - before

    _set_last_created(conn, meta_key, newest)
    conn.commit()
    return counts


def sync_if_configured(conn: sqlite3.Connection, user: str = "") -> dict | None:
    """Called by the refresh loop. Never raises: a Stripe problem must not stop the receipt."""
    settings = stripe_settings()
    if not settings:
        return None
    try:
        return sync(conn, settings, user=user)
    except Exception as exc:
        print(f"[stripe] sync failed: {exc!r}; will retry next refresh", file=sys.stderr)
        return {"error": repr(exc)}


if __name__ == "__main__":
    from store import connect
    settings = stripe_settings()
    if not settings:
        print("Not configured: add RECEIPT_STRIPE_TEST_KEY=sk_test_... to .env (docs/STRIPE.md).")
        sys.exit(1)
    conn = connect()
    print(sync(conn, settings))
    conn.close()

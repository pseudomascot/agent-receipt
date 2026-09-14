"""Simulate the agent using its own card, in Stripe test mode (fake money).

    ./.venv/bin/python examples/simulate_card_purchase.py                 # $150.00 at a fake merchant
    ./.venv/bin/python examples/simulate_card_purchase.py 12.34 "STARBUCKS" --declare

Creates (once) a test cardholder and virtual card under your Stripe test
account, then uses Stripe's test helper to authorize a purchase on it. With
--declare, also writes a receipt line with the authorization id, so the
receipt attributes the purchase to the agent instead of by evidence.
Requires Issuing to be available in test mode on your account.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import stripe_settings  # noqa: E402
from stripe_connector import StripeError, request, request_v2  # noqa: E402

settings = stripe_settings()
if not settings:
    sys.exit("Not configured: add RECEIPT_STRIPE_TEST_KEY=sk_test_... to .env (docs/STRIPE.md).")
if settings["mode"] != "test":
    sys.exit("Refusing to simulate on a live key. Use a test key (sk_test_...).")
key = settings["key"]

args = [a for a in sys.argv[1:] if not a.startswith("--")]
amount = float(args[0]) if args else 150.00
merchant = args[1] if len(args) > 1 else "ACME CLOUD SERVICES"

def _financial_account_id():
    """Newer sandboxes fund cards from a v2 financial account; cards must name it."""
    try:
        fas = request_v2(key, "GET", "/v2/money_management/financial_accounts").get("data", [])
    except StripeError:
        return None
    return fas[0]["id"] if fas else None


def _cardholder():
    """Reuse a complete test cardholder, or create one with everything Issuing requires."""
    for h in request(key, "GET", "/v1/issuing/cardholders", {"limit": 10, "status": "active"}).get("data", []):
        req = h.get("requirements") or {}
        if not req.get("past_due") and not req.get("disabled_reason"):
            return h
    return request(key, "POST", "/v1/issuing/cardholders", {
        "name": "Agent Receipt Test", "type": "individual", "status": "active",
        "email": "agent-receipt-test@example.com", "phone_number": "+18005550123",
        "individual": {
            "first_name": "Agent", "last_name": "Receipt",
            "dob": {"day": 1, "month": 1, "year": 1990},
            "card_issuing": {"user_terms_acceptance": {"date": int(time.time()), "ip": "127.0.0.1"}},
        },
        "billing": {"address": {"line1": "1 Test St", "city": "San Francisco", "state": "CA",
                                "postal_code": "94111", "country": "US"}},
    })


try:
    cards = request(key, "GET", "/v1/issuing/cards", {"limit": 1, "status": "active"}).get("data", [])
    if cards:
        card = cards[0]
    else:
        holder = _cardholder()
        card_params = {"cardholder": holder["id"], "currency": "usd", "type": "virtual", "status": "active"}
        fa_id = _financial_account_id()
        if fa_id:
            card_params["financial_account_v2"] = fa_id
        card = request(key, "POST", "/v1/issuing/cards", card_params)
        print(f"created test cardholder {holder['id']} and virtual card ending {card.get('last4')}")
    auth = request(key, "POST", "/v1/test_helpers/issuing/authorizations", {
        "card": card["id"], "amount": int(round(amount * 100)), "currency": "usd",
        "merchant_data": {"name": merchant, "category": "computer_software_stores",
                          "city": "San Francisco", "country": "US"},
    })
except StripeError as exc:
    if "not set up to use Issuing" in str(exc):
        sys.exit(f"Stripe said: {exc}\nEnable Issuing in the sandbox first (Dashboard → Issuing → "
                 "Explore in sandbox). Charges are still recorded without it.")
    sys.exit(f"Stripe said: {exc}")

print(f"authorized ${amount:.2f} at {merchant} on card ending {card.get('last4')}: {auth['id']} ({auth.get('status')})")

if "--declare" in sys.argv:
    from receipt_line import record
    record("purchase", target=merchant, agent=settings["agent"], id=auth["id"], amount=amount,
           currency="USD", reversible=False, reason="a card charge can only be disputed",
           link=f"https://dashboard.stripe.com/test/issuing/authorizations/{auth['id']}")
    print("declared via receipt line")

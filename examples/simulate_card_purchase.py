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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import stripe_settings  # noqa: E402
from stripe_connector import StripeError, request  # noqa: E402

settings = stripe_settings()
if not settings:
    sys.exit("Not configured: add RECEIPT_STRIPE_TEST_KEY=sk_test_... to .env (docs/STRIPE.md).")
if settings["mode"] != "test":
    sys.exit("Refusing to simulate on a live key. Use a test key (sk_test_...).")
key = settings["key"]

args = [a for a in sys.argv[1:] if not a.startswith("--")]
amount = float(args[0]) if args else 150.00
merchant = args[1] if len(args) > 1 else "ACME CLOUD SERVICES"

try:
    cards = request(key, "GET", "/v1/issuing/cards", {"limit": 1, "status": "active"}).get("data", [])
    if cards:
        card = cards[0]
    else:
        holder = request(key, "POST", "/v1/issuing/cardholders", {
            "name": "Agent Receipt Test", "type": "individual", "status": "active",
            "email": "agent-receipt-test@example.com",
            "billing": {"address": {"line1": "1 Test St", "city": "San Francisco", "state": "CA",
                                    "postal_code": "94111", "country": "US"}},
        })
        card = request(key, "POST", "/v1/issuing/cards", {
            "cardholder": holder["id"], "currency": "usd", "type": "virtual", "status": "active"})
        print(f"created test cardholder {holder['id']} and virtual card ending {card.get('last4')}")
    auth = request(key, "POST", "/v1/test_helpers/issuing/authorizations", {
        "card": card["id"], "amount": int(round(amount * 100)), "currency": "usd",
        "merchant_data": {"name": merchant, "category": "computer_software_stores",
                          "city": "San Francisco", "country": "US"},
    })
except StripeError as exc:
    if exc.status in (400, 403):
        sys.exit(f"Stripe said: {exc}\nIssuing is probably not enabled in test mode on this account "
                 "(Dashboard → Issuing → get started). Charges will still be recorded.")
    raise

print(f"authorized ${amount:.2f} at {merchant} on card ending {card.get('last4')}: {auth['id']} ({auth.get('status')})")

if "--declare" in sys.argv:
    from receipt_line import record
    record("purchase", target=merchant, agent=settings["agent"], id=auth["id"], amount=amount,
           currency="USD", reversible=False, reason="a card charge can only be disputed",
           link=f"https://dashboard.stripe.com/test/issuing/authorizations/{auth['id']}")
    print("declared via receipt line")

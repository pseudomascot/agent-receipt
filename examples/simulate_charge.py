"""Simulate the agent collecting a payment, in Stripe test mode (fake money).

    ./.venv/bin/python examples/simulate_charge.py                    # $150.00 from "Jane Client"
    ./.venv/bin/python examples/simulate_charge.py 42.10 "Acme LLC" --declare

Confirms a PaymentIntent with Stripe's test card (pm_card_visa), which
produces a succeeded charge the connector records as money collected.
With --declare, also writes a receipt line with the charge id so the receipt
attributes it to the agent. Refuses live keys.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import stripe_settings  # noqa: E402
from stripe_connector import request  # noqa: E402

settings = stripe_settings()
if not settings:
    sys.exit("Not configured: add RECEIPT_STRIPE_TEST_KEY=sk_test_... to .env (docs/STRIPE.md).")
if settings["mode"] != "test":
    sys.exit("Refusing to simulate on a live key. Use a test key (sk_test_...).")

args = [a for a in sys.argv[1:] if not a.startswith("--")]
amount = float(args[0]) if args else 150.00
customer = args[1] if len(args) > 1 else "Jane Client"

intent = request(settings["key"], "POST", "/v1/payment_intents", {
    "amount": int(round(amount * 100)), "currency": "usd", "payment_method": "pm_card_visa",
    "confirm": "true", "description": f"Agent Receipt test charge for {customer}",
    "automatic_payment_methods": {"enabled": "true", "allow_redirects": "never"},
})
charge_id = intent.get("latest_charge")
print(f"charged ${amount:.2f} for {customer}: intent {intent['id']} ({intent.get('status')}), charge {charge_id}")

if "--declare" in sys.argv and charge_id:
    from receipt_line import record
    record("other", target=f"charged {customer}", agent=settings["agent"], id=charge_id, amount=amount,
           currency="USD", reversible=True, reason="can be refunded",
           link=f"https://dashboard.stripe.com/test/payments/{charge_id}")
    print("declared via receipt line")

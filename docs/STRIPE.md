# Stripe (card spend and charges collected)

## What it does
- Polls the Stripe API every refresh with the key in `.env` (standard library, no SDK).
- **Issuing authorizations** — the agent's own virtual card being used —
  become `purchase` rows (source `card`) with merchant, amount, currency, the
  card's last four digits, and a link to the authorization in the dashboard.
  Timestamped by Stripe's event time, so the 30-second attribution window is
  measured against when the card was actually used.
- **Charges** — money the agent collected from someone else's card (a pay
  link, an invoice) — become `other` rows with an amount, so they count in
  totals and trip the money rule. Reversible: yes (refund).
- Stripe ids are the row ids. A receipt line (docs/RECEIPT_LINE.md) declaring
  the same id merges into the row and attributes it to the agent.

## Test mode (fake money, real API)
1. Stripe Dashboard → toggle **Test mode** → Developers → API keys → copy the
   **Secret key** (`sk_test_…`).
2. `.env`: `RECEIPT_STRIPE_TEST_KEY=sk_test_…` (the file is git-ignored).
3. Restart `Agent Receipt.command`. The coverage row switches to "covered".
4. Simulate the agent buying something on its own card:
   ```bash
   ./.venv/bin/python examples/simulate_card_purchase.py                # $150 → purchase + Needs review
   ./.venv/bin/python examples/simulate_card_purchase.py 12.34 "STARBUCKS" --declare   # attributed to the agent
   ./.venv/bin/python src/stripe_connector.py                           # poll now
   ```
   The first run creates a test cardholder and virtual card. Needs Issuing to
   be available in test mode (Dashboard → Issuing); if it isn't, the script
   says so and charges are still recorded.

## Live mode
`RECEIPT_STRIPE_KEY=sk_live_…` works the same way, read-only: the connector
only ever lists authorizations and charges. The simulate script refuses live
keys. Consider a restricted key with read access to Issuing and Charges only.

## Never stored
Card numbers. Stripe only exposes the last four digits, and that is all the
receipt keeps.

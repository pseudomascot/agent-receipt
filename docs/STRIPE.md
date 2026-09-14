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
   be available in test mode (Dashboard → Issuing → get started); if it isn't,
   the script says so and charges are still recorded.
5. Simulate the agent collecting a payment (works without Issuing):
   ```bash
   ./.venv/bin/python examples/simulate_charge.py                       # $150 from a test card → money alert
   ./.venv/bin/python examples/simulate_charge.py 42.10 "Acme LLC" --declare   # attributed to the agent
   ```
   The first sync also imports the account's existing test-mode charge
   history, attributed by evidence (usually `unknown`).

## Sandboxes with a v2 financial account (learned 2026-09-14)
Newer sandboxes ("Explore in sandbox" on the Issuing page) fund cards from a
**v2 financial account** rather than the classic Issuing balance:
- The sandbox has its **own** secret key (Developers → API keys inside the
  sandbox); the classic test-mode key returns "not set up to use Issuing".
- v2 endpoints need `Stripe-Version: <date>.preview` (see `request_v2`).
- Cards must be created with `financial_account_v2=<fa_test_…>`; the
  cardholder needs `individual.first_name/last_name`, a `dob`, and
  `individual.card_issuing.user_terms_acceptance` or Stripe reports
  `requirements.past_due`.
- Cards cannot be created while the financial account is `pending`. On an
  account that has not completed live onboarding, the sandbox's financial
  account stays pending: the Setup guide's "Create your Stripe profile" step
  says "To continue, switch to live mode — this feature can't be set up in
  your sandbox account." In other words, Stripe gates an agent's own card
  behind full business verification even for testing. The own-card path is
  implemented and verified up to that gate; run it on an Issuing-activated
  account.
- `POST /v1/topups` with `destination_balance=issuing` is rejected on these
  accounts, and the dashboard's Add funds only offers the refunds/disputes
  balance; fund the financial account once it is open.
The simulate script handles all of the above automatically once the account
is open.

## Live mode
`RECEIPT_STRIPE_KEY=sk_live_…` works the same way, read-only: the connector
only ever lists authorizations and charges. The simulate script refuses live
keys. Consider a restricted key with read access to Issuing and Charges only.

## Never stored
Card numbers. Stripe only exposes the last four digits, and that is all the
receipt keeps.

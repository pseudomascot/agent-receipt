# Ramp: a card and a budget per agent

Ramp is the first "issuer" connector: instead of only *watching* a card the
agent already has, the receipt can **hand an agent its own budget and virtual
card**, attribute every purchase on it to that agent by construction, and cut
it off in one call. Sandbox only until you say otherwise — `demo-api.ramp.com`
moves no real money.

## Why Ramp's shape fits
Ramp's primitive is the **fund**: a budget with restrictions (amount,
interval — daily / weekly / monthly / total — allowed merchant categories)
that **issues a virtual card automatically** and can be **suspended,
unsuspended or terminated** in one API call. One fund per agent gives you
exactly "one agent, one card, one budget, one kill switch", and every
transaction carries the `fund_id`, so attribution needs no guessing.

Verified against Ramp's own OpenAPI spec on 2026-09-15 (the spec is at
`https://docs.ramp.com/openapi/developer-api.json`; the older
`cards/deferred/virtual` endpoint people still mention online is gone):

| What | Endpoint |
|---|---|
| Token | `POST /developer/v1/token` — HTTP Basic `client_id:client_secret`, **form-encoded** body `grant_type=client_credentials&scope=…` (JSON is rejected — confirmed live) |
| Issue a fund | `POST /developer/v1/funds` (`user_id`, `display_name`, `spending_restrictions.limit{amount (cents), currency_code}`, `interval`, `permitted_spend_types`) — the response lists the card(s) with `last_four` |
| Suspend / unsuspend | `POST` / `DELETE /developer/v1/funds/{id}/suspension` |
| Terminate | `DELETE /developer/v1/funds/{id}` |
| Read purchases | `GET /developer/v1/transactions?from_date=…` (paged via `page.next`); each has `fund_id`, `card_id`, `merchant_name`, amount, `state` incl. `DECLINED` with `decline_details.reason` |
| Users | `GET /developer/v1/users` — cards belong to a Ramp user |

Scopes the app's OAuth client needs: `funds:read funds:write transactions:read users:read cards:read`.

## Setup (sandbox) — only you can do these
1. **Get sandbox access:** https://docs.ramp.com/developer-api/v1/sandbox-access
   (Ramp grants a demo business at https://demo.ramp.com).
2. In the sandbox, **create a Developer API app** with the *client credentials*
   grant and the scopes above. Copy its client id and secret.
3. Paste them into the git-ignored `.env`:
   ```
   RECEIPT_RAMP_CLIENT_ID=…
   RECEIPT_RAMP_CLIENT_SECRET=…
   RECEIPT_RAMP_ENV=sandbox
   ```
4. **Pick the Ramp user who holds the agents' cards.** Ramp cards belong to a
   person. Create a user called e.g. "AI Agents" in the sandbox (or use your own
   demo user), then run
   ```bash
   .venv/bin/python examples/ramp_users.py
   ```
   and put that user's id into `.env` as `RECEIPT_RAMP_USER_ID`.
5. Restart Agent Receipt. The front page's "This machine" table shows Ramp as
   configured; Coverage gains a "Card purchases on Ramp" line.

**Simulating a purchase in the sandbox:** in https://demo.ramp.com press ⌘ J
to open Ramp's demo-actions panel and choose "add transactions". Within five
minutes it appears on the receipt.

## What the app does with it
- **Agents page → "Give a card".** Each active agent gets a budget box
  (amount + per day / week / month / total) and a *Give a card* button. The app
  creates a fund named `Agent · <the agent's name>` (virtual card only), stores
  the fund ↔ agent mapping in `ramp_funds`, and writes "Gave Bookkeeping bot a
  Ramp card: 500.00 USD per monthly, card •••• 4242 (sandbox)" to the receipt
  as *your* action. The row then shows the card and its state.
- **Statements.** Every Ramp transaction becomes a *purchase* row, at Ramp's
  own transaction time, linked to the transaction in Ramp. On a fund the app
  issued it is attributed to that agent **by credential** (like the agent's
  own Google account) — no keyboard guessing. Anything else on the Ramp account
  is a `card` row under the label `ramp card (sandbox)`, attributed by the
  usual correlation. **Declines are recorded** ("DECLINED: BIG SPEND — declined
  by Ramp: AUTHORIZER_CARD_LIMIT"): an agent trying to spend past its budget is
  evidence, not noise.
- **Stop page.** Each issued fund gets *Suspend the agent's Ramp card*
  (instant, reversible), *Unsuspend*, and *Terminate (permanent)*. **Freeze
  all** suspends every agent fund and never terminates.
- **Money page / bookkeeping CSV** include Ramp purchases like any other.

## Honest limits
- **Live status (2026-09-15, sandbox):** token ✓ (form-encoded body), users ✓,
  funds ✓, **issue a fund + virtual card ✓** (card •••• 5039, $25/day, named
  "Agent · Sub-agent of Claude Code"), **suspend ✓ / unsuspend ✓** from the
  Stop page, **transaction sync ✓** (91 demo purchases). Purchases on a card we
  did not issue are attributed to the named card holder *by credential*
  (attribution `person`), not left "unexplained" — otherwise every employee
  coffee would be a Needs-review alert. A purchase on the agent's own fund is
  the last thing to see live (make one in the sandbox with ⌘J on card 5039).
- **Ramp cards belong to a user.** All agent funds hang off the one Ramp user
  in `RECEIPT_RAMP_USER_ID`. Identity lives in the *fund*, which is what
  transactions carry, so this is fine for attribution — but Ramp's own UI will
  show that user as the cardholder.
- **Budgets are enforced by Ramp, not by the app.** The app sets the limit
  when it issues the fund and can suspend; it does not sit in the
  authorization path. Ramp's own approval policies still apply.
- **Production** needs a real Ramp business (`RECEIPT_RAMP_ENV=production`)
  and a production app, and Ramp's production review for any client that
  creates funds. Sandbox first.
- Sub-agents: give a worker its own card from the Agents page the same way —
  it is just another identity there.

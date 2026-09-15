# Bookkeeping and taxes

## The short version
Agent Receipt does not file taxes and does not talk to TurboTax. The money an
agent spends or collects is already on your bank, card or Stripe statement —
that is what tax software imports, and importing it twice would double-count.

What the receipt adds is the part those statements lack: **who** spent or
received the money (which agent, under which login), **for which project**,
and **whether anyone was at the keyboard**. That is what a bookkeeper needs
to categorise the transaction (business or personal, which client, deductible
or not) and what you would show an auditor or an insurer.

So the path to a tax return is: receipt → your bookkeeper / QuickBooks / Xero
→ tax software. Not receipt → TurboTax.

## The Money page
`http://127.0.0.1:8765/money` lists only the rows that carry an amount,
grouped by month, newest first, with spent / received / net per currency.
Every row links to its day on the statement. **Print** gives a paper or PDF
statement for an accountant. `/money/<start>/<end>` narrows the range; the
usual `?agent=` and `?user=` filters apply.

Direction: a **purchase** is money out (card alerts, the agent's own card).
Anything else with an amount — a Stripe charge collected — is money in.

## The QuickBooks / Xero file
**Download for QuickBooks / Xero** on the Money page produces the plain
three-column layout both accept as a bank-statement import:

```
Date,Description,Amount
09/14/2026,Paid 150.00 USD at ACME CLOUD SERVICES · agent: cowork · project: acme-site · done by the agent · ref: card:<id>,-150.00
09/14/2026,Received: charged Jane Client · agent: stripe · done by the agent · ref: ch_3UF…,150.00
```

- Dates are `MM/DD/YYYY`; money out is negative; chronological.
- One currency per file. If your rows carry more than one currency, the page
  shows one download button per currency.
- No footer rows — the importers reject them. The full `Download CSV`
  (verifiable, with the SHA-256 footer) contains the same rows if you need
  the checksummed version.

How to use it without double counting — in QuickBooks Online: Transactions →
Bank transactions → Upload from file; then for each imported line use
**Match** against the real bank/card transaction rather than **Add**. Xero:
Bank account → Manage → Import a statement, then Reconcile → Match. The
description carries the agent, project, attribution and reference, so the
matched transaction keeps that context as its memo.

## Not built (on purpose, until someone asks)
- A direct QuickBooks Online API connection that writes the agent context as
  a memo on the matching transaction. Doable; needs OAuth per user.
- Direct tax-software import. There is no public door for it, and the numbers
  are already there via the bank feed.

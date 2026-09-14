# Email and card alerts (v2, first connector)

## What it does
- Polls the agent's own mailbox over IMAP (read-only) every refresh.
- Every message in the **Sent** folder becomes a `send_email` row (source
  `email`): time, recipients, subject and first line kept locally.
- Every message in the **alerts** folder (INBOX by default) that looks like a
  card issuer's transaction alert becomes a `purchase` row (source `card`) with
  merchant, amount, currency, and the last four digits the issuer printed.
  Nothing else from the inbox is stored. Card numbers are never stored.
- Attribution is decided by the correlator, not the mailbox: with physical
  input in the prior 30 s and no agent log → `human`; with an agent log (or a
  receipt line, see docs/RECEIPT_LINE.md) → `agent`; neither → `unknown`.

## Why a separate mailbox
If the agent sends from your own address, a sent message says nothing about
who sent it. Give the agent its own mailbox (or a send-as alias) and the
statement can tell you apart.

## Setup (Gmail; any IMAP provider works)
1. Create a throwaway Gmail account for the agent.
2. Google Account → Security → 2-Step Verification: on. Then App passwords →
   create one for "Mail". Copy the 16-character password.
3. Gmail → Settings → Forwarding and POP/IMAP → Enable IMAP.
4. In this folder: `cp .env.example .env`, then fill in `RECEIPT_IMAP_USER` and
   `RECEIPT_IMAP_PASSWORD`. `.env` is git-ignored.
5. Restart `Agent Receipt.command`. The coverage table at the bottom of every
   page switches the email and card rows to "covered".

## Testing it
```bash
./.venv/bin/python examples/send_test_email.py                 # a sent mail with no agent log → human/unknown
./.venv/bin/python examples/send_test_email.py --declare       # the same, declared via receipt line → agent
./.venv/bin/python examples/send_fake_card_alert.py            # a $150 fake alert → purchase + Needs review
./.venv/bin/python src/email_connector.py                      # poll now instead of waiting 5 minutes
```

## Limits
- Alert parsing is pattern-based (Chase, Amex, Capital One phrasings and two
  generic forms). An unrecognised alert is simply not a purchase; add its
  phrasing to `src/card_alerts.py`.
- Gmail's IMAP UIDs are stable per folder; if a folder is recreated the
  position resets and old mail may be re-read (harmless: message ids dedup).

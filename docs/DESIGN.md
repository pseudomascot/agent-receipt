# Design reasoning

This exists so future sessions don't re-argue settled questions. It's the condensed version of the chat that produced this project.

## Why a receipt, not an undo layer
The original idea was an undo layer: hold every agent action for a window, cancel if needed. That only works if every action is routed through it, which is an adoption problem. A receipt is read-only, breaks nothing if it's incomplete, and people accept "show me what it did" long before they accept "hold everything it does." The undo becomes a later feature: a receipt line with a timer and a cancel button. Same plumbing.

The analogy: nobody trusted banks because of the vault. They trusted the statement.

## Why attribution can't be inferred after the fact
An email sent by an agent through an API and an email sent by a person in a browser look identical to the receiving service: same headers, same message ID format. A card transaction shows merchant, amount, time, and card-present or not. Nothing says who typed it. Crypto: one key, one signer.

Writing style and timing give maybe 80% accuracy. An 80% receipt is worse than none, because people will act on it.

Visa and Mastercard hit the same wall and solved it by issuing agent-specific credentials (Mastercard Agentic Tokens, Visa Trusted Agent Protocol, the joint Know Your Agent framework). They tag at the credential, not by detection. As those rails roll out, agent purchases will carry an agent marker by construction, and the receipt reads it.

## The two regimes
1. **Your own receipt, your own machine.** Works, and two people can build it. The OS already distinguishes injected input from physical input (Windows flags injected events; macOS tags event sources). Log physical input timestamps, pair with the agent's own tool-call logs, classify each action. Certain, not guessed, because your own agent isn't trying to hide from you. Only covers this machine; cloud or scheduled agents need their logs.
2. **Proof anyone else can trust.** A software log that says "physical click" is forged by the software you're trying to catch, and a $10 USB board produces physically real input. The robust version is hardware-attested per-action proof (a secure chip signs "a human touched this for this action"). The closest existing primitive is a passkey or security key touch. Extending it everywhere needs OS vendors, browsers, and every service. Not a two-person project. If it ever ships, the receipt ingests it.

We build regime 1.

## Sources by channel (v2 detail)
- **Email.** Agent gets its own mailbox. Read-only OAuth scope or IMAP app password. Poll Sent since last check. Each message: time, recipient, subject, first line, link. If the agent sends from the person's own address, attribution is impossible; a send-as alias in the same inbox is the light fix.
- **Card.** Turn on the issuer's per-transaction alerts, route them to a mailbox the collector reads, parse: merchant, amount, last four, time. Brittle but universal. Business cards (Ramp, Brex, Stripe Issuing) have webhooks, which are cleaner. A virtual card number on the same account is the light separation.
- **Crypto.** Agent gets its own address. Poll a block explorer or RPC node. Complete data, but counterparty is an address, not a name. Keep a label table.
- **Calendar.** Read-only poll for created and deleted events.
- **Not covered by statements at all:** posting to websites, form submissions, file deletes, settings changes. Only agent logs or a proxy catch these. The statement page must say what isn't covered.

## Product shape
Desktop app (menu bar / tray) as the local collector, web page for the statement, connectors for email, card, wallet. Open source so people will install something shaped like a keylogger. Timestamps only, never content.

Not an extension (sees only the browser). Not built to sell to a platform; a platform's own activity log is the audited party grading itself. Independence across agents is the product. If Anthropic ships an activity log, ingest it as one more source.

## Who pays
Not consumers. Small businesses and professionals whose insurer, regulator, or liability makes "show me exactly what the AI did" a requirement. Price like bookkeeping software. Individual use free; business version is the revenue.

## Trust hazards to design around
- A receipt with silent gaps is worse than no receipt. Label coverage explicitly on the page.
- Never log keystroke content, mouse positions, screenshots, or window titles by default.
- Never store card numbers.
- Never upload anything in v1.

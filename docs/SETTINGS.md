# Settings page

`http://127.0.0.1:8765/settings` — connect the optional pieces from inside
the app instead of editing a text file. Nothing on it is required: with no
settings at all the receipt still covers every agent on the Mac (Claude Code,
Cowork, Codex, Cursor, and anything writing receipt lines).

One card per connector — **Agent mailbox**, **Stripe**, **Ramp**, **Google
Calendar**, **Mac Calendar** — each with:

- the fields it needs, a one-line explanation, and a link to where the value
  comes from (Gmail app passwords, Stripe API keys, Ramp's developer page…);
- **Save** — writes to the same private `.env` file next to the project
  (git-ignored, owner-only permissions). Comments and other lines in the file
  are left alone, so hand-editing still works;
- **Test** — saves, then makes one read-only call and reports in plain
  English: "connected to imap.gmail.com as …: 3 messages in Sent", "Stripe
  refused: 401", "signed in: 2 calendars visible", "macOS has not allowed
  Agent Receipt to control Calendar yet — System Settings → …";
- for Google Calendar, **Sign in as the agent** runs the one-time consent in
  the browser; for Ramp, the **Card holder** becomes a dropdown of the
  account's users once the credentials are saved.

Rules the page keeps:
- **Secrets are never shown again.** After saving, a password or key field
  shows "•••••••• saved — leave blank to keep". A "remove" box clears it.
- **Every save is on the receipt** as a line like *Changed settings: Agent
  mailbox (RECEIPT_IMAP_USER, RECEIPT_IMAP_PASSWORD)* — field names only, never
  values — attributed to the person who pressed Save.
- Placeholder values left over from `.env.example` (`sk_test_...`,
  `PASTE-CLIENT-ID`) count as "not set up".
- The running app picks up changes on its next refresh (within five
  minutes); the Test button uses them immediately.

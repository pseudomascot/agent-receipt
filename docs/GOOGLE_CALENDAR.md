# Google Calendar (v2 connector)

## Why this one, and why alongside the Mac Calendar connector
Google's API gives what the Mac's Calendar app can't: each event's **creation
time**, **last-updated time**, and the **account that created it**. If the
agent has its own Google account (the same idea as its own mailbox), events it
creates are attributed to it by credential — no guessing. List those accounts
in `RECEIPT_GOOGLE_AGENT_EMAILS`.

## What becomes a row
- new event → `create_event`, timestamped at its real creation time, linked to
  the event in Google Calendar
- changed event → "Changed a calendar event"
- deleted event → "Deleted a calendar event" (irreversible)
- The first sync only takes a baseline. Afterwards Google's incremental sync
  token is used, so each poll fetches only what changed.
- Created by an agent account → agent (source `log`, note "created by the
  agent's own Google account"). Otherwise attributed by evidence like email.

## Setup — the part only you can do (about ten minutes, once)
Google requires an "OAuth client" to let any app read a calendar.

1. Go to https://console.cloud.google.com/ and sign in (any Google account;
   the project is just a container).
2. Create a project (top-left project picker → New project), e.g.
   "Agent Receipt".
3. **APIs & Services → Library** → search "Google Calendar API" → **Enable**.
4. **APIs & Services → OAuth consent screen** → External → fill in an app
   name ("Agent Receipt") and your email → Save. Under **Test users**, add the
   agent's Gmail address (e.g. `agent.mailbox@gmail.com`). While the app is
   in "Testing" only listed test users can sign in — that is fine.
5. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   Application type **Desktop app** → Create. Copy the **Client ID** and
   **Client secret**.
6. `.env`:
   ```
   RECEIPT_GOOGLE_CLIENT_ID=….apps.googleusercontent.com
   RECEIPT_GOOGLE_CLIENT_SECRET=…
   RECEIPT_GOOGLE_AGENT_EMAILS=agent.mailbox@gmail.com
   RECEIPT_GOOGLE_CALENDARS=primary
   ```
7. Sign the agent's account in, one time:
   ```bash
   ./.venv/bin/python examples/google_calendar_auth.py
   ```
   A browser opens; sign in **as the agent's Google account**, click Allow.
   The refresh token is saved to `~/.agent-receipt/google_token.json`
   (private to your user; not in the repo).
8. Restart Agent Receipt. The coverage row switches to "covered".

## Testing it
Create an event in the agent account's calendar (calendar.google.com signed
in as the agent, or through any tool acting as that account) → within five
minutes: "Created a calendar event: …", attributed to the agent by credential.
Create one from your own account on a calendar the agent can see → attributed
by evidence.

## What leaves the machine
Only calls to Google's Calendar API with the agent account's token, read-only
scope (`calendar.events.readonly`). Nothing else.

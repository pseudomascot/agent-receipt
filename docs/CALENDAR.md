# Calendar (v2 connector)

## What it does
- Watches the **macOS Calendar app** — and therefore every calendar it syncs
  (iCloud, Google, Exchange, local) — by asking the app through AppleScript.
  Local and read-only; nothing leaves the machine.
- Every refresh it compares the calendar with what it saw last time:
  - a new event → `create_event`
  - a changed event → "Changed a calendar event"
  - a vanished event → "Deleted a calendar event" (irreversible)
- The first run only takes a baseline; existing events are not reported as new.
- Attribution is decided by the correlator, like email: typing or clicking
  just before → `person`; a receipt line (docs/RECEIPT_LINE.md) declaring
  `cal:<event id>` → `agent`; neither → `unexplained`.
- Watched window: 30 days back to 365 days ahead (agents schedule the future).
  Subscribed/system calendars (holidays, birthdays, Siri suggestions) are
  skipped unless named in `RECEIPT_CALENDARS`.

## Setup
1. `.env`: `RECEIPT_CALENDAR=on`. Optionally `RECEIPT_CALENDARS=Work,Agent Receipt Test`
   to watch only those calendars.
2. Restart Agent Receipt. On the first poll macOS asks: **"Agent Receipt wants
   access to control Calendar"** — allow. (Launched from Terminal, the prompt
   names Terminal.) To change your mind later: System Settings → Privacy &
   Security → Automation.
3. The coverage table's Calendar row switches to "covered".

## Testing it
```bash
./.venv/bin/python examples/create_test_event.py                 # by script, undeclared → person/unexplained
./.venv/bin/python examples/create_test_event.py "Dentist" --declare   # declared → agent
./.venv/bin/python src/calendar_connector.py                     # poll now instead of waiting 5 minutes
```
Or add, rename, and delete an event by hand in the Calendar app and watch the
three kinds of rows appear on the next refresh.

## Limits (honest ones)
- Calendar's scripting interface has no *creation* time, so a new event is
  timestamped when the receipt first sees it — up to five minutes after it
  was made. The 30-second attribution window is measured against that, so
  calendar rows lean toward `unexplained` unless the agent declares them.
- Each poll takes 10–20 seconds because the Calendar app answers slowly (it
  opens in the background if it wasn't running).
- Why not Apple's EventKit: on current macOS it silently refuses a Python
  process that is not a signed app carrying a calendar usage description —
  no prompt, no error. Tried and documented 2026-09-15; a compiled, signed
  helper would fix it and is not worth it yet.

## Not stored
Event notes and attendee lists are not read; the statement shows title, time,
location, and calendar name.

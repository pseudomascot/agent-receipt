# Calendar (v2 connector)

## What it does
- Watches the **macOS Calendar app** — and therefore every calendar it syncs
  (iCloud, Google, Exchange, local) — through Apple's EventKit. Local and
  read-only; nothing leaves the machine.
- Every refresh it compares the calendar with what it saw last time:
  - a new event → `create_event` (timestamped by the event's creation time)
  - a changed event → "Changed a calendar event"
  - a vanished event → "Deleted a calendar event" (irreversible)
- The first run only takes a baseline; existing events are not reported as new.
- Attribution is decided by the correlator, like email: typing or clicking
  just before → `person`; a receipt line (docs/RECEIPT_LINE.md) declaring
  `cal:<event id>` → `agent`; neither → `unexplained`.
- Watched window: 30 days back to 365 days ahead (agents schedule the future).

## Setup
1. `.env`: `RECEIPT_CALENDAR=on`. Optionally `RECEIPT_CALENDARS=Work,Agent Receipt Test`
   to watch only some calendars.
2. Restart Agent Receipt. macOS asks once: "Agent Receipt would like to access
   your calendar" — allow. (If you launch from Terminal, the prompt names
   Terminal.) To change your mind later: System Settings → Privacy & Security →
   Calendars.
3. The coverage table's Calendar row switches to "covered".

## Testing it
```bash
./.venv/bin/python examples/create_test_event.py                 # by script, undeclared → person/unexplained
./.venv/bin/python examples/create_test_event.py "Dentist" --declare   # declared → agent
./.venv/bin/python src/calendar_connector.py                     # poll now instead of waiting 5 minutes
```
Or add, rename, and delete an event by hand in the Calendar app and watch the
three kinds of rows appear.

## Not stored
Event notes and attendee email addresses are kept only in the row's local
raw data; the statement shows title, time, and calendar name.

"""Create a test event in the macOS Calendar app, to exercise the calendar connector.

    ./.venv/bin/python examples/create_test_event.py                 # "Agent Receipt test" tomorrow at 10:00
    ./.venv/bin/python examples/create_test_event.py "Dentist" --declare

Creates (once) a local calendar named "Agent Receipt Test" and adds the event
there. With --declare, also writes a receipt line with the event's id, so the
receipt attributes the creation to the agent instead of by evidence.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from calendar_connector import request_access  # noqa: E402

if not request_access():
    sys.exit("Calendar access not granted: System Settings → Privacy & Security → Calendars.")

from EventKit import EKEntityTypeEvent, EKEvent, EKEventStore, EKSpanThisEvent  # noqa: E402
from Foundation import NSDate  # noqa: E402

CALENDAR = "Agent Receipt Test"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
title = args[0] if args else "Agent Receipt test"

store = EKEventStore.alloc().init()
calendar = next((c for c in store.calendarsForEntityType_(EKEntityTypeEvent) if str(c.title()) == CALENDAR), None)
if calendar is None:
    from EventKit import EKCalendar
    calendar = EKCalendar.calendarForEntityType_eventStore_(EKEntityTypeEvent, store)
    calendar.setTitle_(CALENDAR)
    source = next((s for s in store.sources() if int(s.sourceType()) == 0), None) or store.defaultCalendarForNewEvents().source()
    calendar.setSource_(source)
    ok, err = store.saveCalendar_commit_error_(calendar, True, None)
    if not ok:
        sys.exit(f"Could not create the '{CALENDAR}' calendar: {err}")
    print(f"created calendar '{CALENDAR}'")

start = time.time() + 86400
start -= start % 3600
event = EKEvent.eventWithEventStore_(store)
event.setTitle_(title)
event.setStartDate_(NSDate.dateWithTimeIntervalSince1970_(start + 10 * 3600 - (start % 86400)))
event.setEndDate_(NSDate.dateWithTimeIntervalSince1970_(start + 11 * 3600 - (start % 86400)))
event.setCalendar_(calendar)
event.setNotes_("Created by examples/create_test_event.py")
ok, err = store.saveEvent_span_commit_error_(event, EKSpanThisEvent, True, None)
if not ok:
    sys.exit(f"Could not save the event: {err}")
print(f"created event '{title}' in '{CALENDAR}': {event.eventIdentifier()}")

if "--declare" in sys.argv:
    from receipt_line import record
    record("create_event", target=title, agent="calendar test agent", id=f"cal:{event.eventIdentifier()}",
           reversible=True, reason="the event can be deleted", detail={"calendar": CALENDAR})
    print("declared via receipt line")

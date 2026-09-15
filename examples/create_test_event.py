"""Create a test event in the macOS Calendar app, to exercise the calendar connector.

    ./.venv/bin/python examples/create_test_event.py                 # "Agent Receipt test" tomorrow at 10:00
    ./.venv/bin/python examples/create_test_event.py "Dentist" --declare

Creates (once) a calendar named "Agent Receipt Test" and adds the event there,
through the Calendar app's AppleScript interface. With --declare, also writes
a receipt line with the event's id, so the receipt attributes the creation to
the agent instead of by evidence.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CALENDAR = "Agent Receipt Test"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
title = (args[0] if args else "Agent Receipt test").replace('"', "'")

script = f"""
tell application "Calendar"
  if not (exists calendar "{CALENDAR}") then
    make new calendar with properties {{name:"{CALENDAR}"}}
  end if
  set startAt to (current date) + 1 * days
  set time of startAt to 10 * hours
  set e to make new event at end of events of calendar "{CALENDAR}" with properties {{summary:"{title}", start date:startAt, end date:startAt + 1 * hours, description:"Created by examples/create_test_event.py"}}
  return uid of e
end tell
"""
proc = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=180)
if proc.returncode != 0:
    sys.exit(f"Calendar refused: {proc.stderr.strip()}\n(System Settings → Privacy & Security → Automation → allow control of Calendar.)")
uid = proc.stdout.strip()
print(f"created event '{title}' in '{CALENDAR}': {uid}")

if "--declare" in sys.argv:
    from receipt_line import record
    record("create_event", target=title, agent="calendar test agent", id=f"cal:{uid}",
           reversible=True, reason="the event can be deleted", detail={"calendar": CALENDAR})
    print("declared via receipt line")

"""Calendar connector: events created, changed, or deleted in the macOS Calendar app.

Reads through the Calendar app's AppleScript interface (osascript), which
covers whatever calendars the Mac syncs (iCloud, Google, Exchange…). Local and
read-only. macOS asks once whether Agent Receipt may control Calendar
("Automation" permission). Opt-in: RECEIPT_CALENDAR=on in .env.

Why not EventKit: on current macOS, EventKit silently refuses a Python process
that is not a signed app with a usage description in its own Info.plist — no
prompt, no error. AppleScript prompts the way Terminal scripts do.

What becomes a row (source 'calendar'):
- a new event      -> create_event (Calendar exposes no creation time, so it is
                      timestamped when first seen; polls run every 5 minutes)
- a changed event  -> other ("changed a calendar event")
- a deleted event  -> other ("deleted a calendar event"), irreversible
Attribution is left to the correlator; a receipt line declaring the same id
(cal:<event uid>) merges in and makes it the agent's.

The AppleScript part is isolated in `read_events`; `sync` works on plain dicts
so it can be tested without a calendar.
"""

import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

from config import calendar_settings

PAST_DAYS = 30
FUTURE_DAYS = 365
OSASCRIPT_TIMEOUT = 180
# Subscribed/system calendars nobody's agent writes to. Overridden by RECEIPT_CALENDARS.
DEFAULT_SKIP = {"Holidays in United States", "US Holidays", "Birthdays", "Siri Suggestions",
                "Scheduled Reminders"}
RS, FS = "\x1e", "\x1f"   # record / field separators inside the script's output


class CalendarAccessError(RuntimeError):
    pass


def _script(calendar_names) -> str:
    if calendar_names:
        names = ", ".join('"' + n.replace('"', '\\"') + '"' for n in calendar_names)
        guard = f"if cname is in {{{names}}} then"
    else:
        skip = ", ".join('"' + n + '"' for n in sorted(DEFAULT_SKIP))
        guard = f"if cname is not in {{{skip}}} then"
    return f"""
    set RS to character id 30
    set FS to character id 31
    tell application "Calendar"
      set now to current date
      set lo to now - {PAST_DAYS} * days
      set hi to now + {FUTURE_DAYS} * days
      set out to ""
      repeat with c in calendars
        set cname to name of c
        {guard}
          set evs to (every event of c whose start date ≥ lo and start date ≤ hi)
          repeat with e in evs
            set loc to ""
            try
              set loc to location of e
              if loc is missing value then set loc to ""
            end try
            set out to out & (uid of e) & FS & (summary of e) & FS & (((start date of e) - now) as string) & FS & (((end date of e) - now) as string) & FS & (allday event of e) & FS & cname & FS & loc & FS & (((stamp date of e) - now) as string) & RS
          end repeat
        end if
      end repeat
      return out
    end tell
    """


def parse_events(output: str, now: float) -> list[dict]:
    """Turn the script's delimited output into event dicts (offsets are relative to `now`)."""
    events = []
    for rec in output.split(RS):
        parts = rec.split(FS)
        if len(parts) < 8 or not parts[0].strip():
            continue
        uid, title, start, end, all_day, calendar, location, modified = parts[:8]

        def rel(v):
            try:
                return now + float(v)
            except ValueError:
                return None

        events.append({
            "id": uid.strip(), "title": title or "(no title)", "start": rel(start), "end": rel(end),
            "all_day": all_day.strip().lower() == "true", "calendar": calendar, "location": location or None,
            "attendees": [], "created": None, "modified": rel(modified),
        })
    return events


def read_events(calendar_names=None) -> list[dict]:
    """Every event in the watched window, as plain dicts, via the Calendar app."""
    now = time.time()
    proc = subprocess.run(["osascript", "-"], input=_script(calendar_names), capture_output=True,
                          text=True, timeout=OSASCRIPT_TIMEOUT)
    if proc.returncode != 0:
        err = proc.stderr.strip()
        if "-1743" in err or "not allowed" in err.lower() or "Not authorized" in err:
            raise CalendarAccessError("Agent Receipt is not allowed to control Calendar "
                                      "(System Settings → Privacy & Security → Automation)")
        raise RuntimeError(err or f"osascript exited {proc.returncode}")
    return parse_events(proc.stdout, now)


def _when(ev: dict) -> str:
    if ev.get("start") is None:
        return ""
    d = datetime.fromtimestamp(ev["start"])
    return d.strftime("%a %b %d") if ev.get("all_day") else d.strftime("%a %b %d %H:%M")


INSERT = """
INSERT OR IGNORE INTO actions
    (timestamp, agent, user, source, action_type, target, amount, currency, artifact_link,
     reversible, attribution, confidence_note, raw_json, source_ref)
VALUES (?, ?, ?, 'calendar', ?, ?, NULL, NULL, NULL, ?, 'unknown', ?, ?, ?)
"""


def sync(conn: sqlite3.Connection, settings: dict, events: list[dict], user: str = "") -> dict:
    """Compare `events` with what was seen last time; record creations, changes, deletions."""
    agent = settings["agent"]
    now = time.time()
    first_run = conn.execute("SELECT 1 FROM meta WHERE key = 'calendar_baselined'").fetchone() is None
    # Only this connector's entries; Google Calendar keeps its own under "gcal:".
    seen = {row[0]: row for row in conn.execute(
        "SELECT identifier, title, start, modified, calendar FROM calendar_seen "
        "WHERE identifier NOT LIKE 'gcal:%'").fetchall()}
    counts = {"created": 0, "changed": 0, "deleted": 0, "tracked": len(events)}
    current_ids = set()

    for ev in events:
        current_ids.add(ev["id"])
        label = f"{ev['title']} — {_when(ev)}" if _when(ev) else ev["title"]
        raw = json.dumps({"source": "calendar", "tool": "calendar:event", "event": ev})
        prior = seen.get(ev["id"])
        if prior is None:
            # On the very first run the calendar's history is baseline, not new actions.
            if not first_run:
                stamp = ev.get("created") or now
                before = conn.total_changes
                conn.execute(INSERT, (stamp, agent, user, "create_event", label, 1,
                                      f"appeared in calendar '{ev['calendar']}'; reversibility: the event can be deleted",
                                      raw, f"cal:{ev['id']}"))
                counts["created"] += conn.total_changes - before
        elif ev.get("modified") and prior[3] and ev["modified"] > prior[3] + 1:
            stamp = ev["modified"]
            before = conn.total_changes
            conn.execute(INSERT, (stamp, agent, user, "other", f"Changed: {label}", 1,
                                  f"changed in calendar '{ev['calendar']}'; reversibility: the change can be edited back",
                                  json.dumps({"source": "calendar", "tool": "calendar:changed", "event": ev,
                                              "previous_title": prior[1]}),
                                  f"cal:{ev['id']}:{int(stamp)}"))
            counts["changed"] += conn.total_changes - before
        conn.execute(
            "INSERT OR REPLACE INTO calendar_seen (identifier, title, start, modified, calendar, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ev["id"], ev["title"], ev.get("start"), ev.get("modified"), ev.get("calendar"), now))

    window_start = now - PAST_DAYS * 86400
    for identifier, (ident, title, start, modified, calendar) in seen.items():
        if identifier in current_ids or (start is not None and start < window_start):
            continue
        before = conn.total_changes
        conn.execute(INSERT, (now, agent, user, "other", f"Deleted: {title}", 0,
                              f"disappeared from calendar '{calendar}'; reversibility: a deleted event is gone",
                              json.dumps({"source": "calendar", "tool": "calendar:deleted",
                                          "event": {"id": ident, "title": title, "start": start, "calendar": calendar}}),
                              f"cal:{identifier}:deleted"))
        counts["deleted"] += conn.total_changes - before
        conn.execute("DELETE FROM calendar_seen WHERE identifier = ?", (identifier,))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('calendar_baselined', ?)", (str(now),))
    conn.commit()
    return counts


def sync_if_configured(conn: sqlite3.Connection, user: str = "") -> dict | None:
    """Called by the refresh loop. Never raises: a calendar problem must not stop the receipt."""
    settings = calendar_settings()
    if not settings:
        return None
    try:
        return sync(conn, settings, read_events(settings["calendars"]), user=user)
    except CalendarAccessError as exc:
        print(f"[calendar] {exc}", file=sys.stderr)
        return {"error": str(exc)}
    except Exception as exc:
        print(f"[calendar] sync failed: {exc!r}; will retry next refresh", file=sys.stderr)
        return {"error": repr(exc)}


if __name__ == "__main__":
    from store import connect
    settings = calendar_settings()
    if not settings:
        print("Not configured: set RECEIPT_CALENDAR=on in .env (docs/CALENDAR.md).")
        sys.exit(1)
    started = time.time()
    events = read_events(settings["calendars"])
    print(f"read {len(events)} event(s) in {time.time() - started:.1f}s")
    conn = connect()
    print(sync(conn, settings, events))
    conn.close()

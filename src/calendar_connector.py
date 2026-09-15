"""Calendar connector: events created, changed, or deleted in the macOS Calendar app.

Reads through Apple's EventKit, which already holds whatever calendars the Mac
syncs (iCloud, Google, Exchange…). Local, read-only, one macOS permission
prompt. Opt-in: RECEIPT_CALENDAR=on in .env.

What becomes a row (source 'calendar'):
- a new event      -> create_event, timestamped by the event's creation time
- a changed event  -> other ("changed a calendar event")
- a deleted event  -> other ("deleted a calendar event"), irreversible
Attribution is left to the correlator; a receipt line declaring the same id
(cal:<event identifier>) merges in and makes it the agent's.

The EventKit part is isolated in `read_events`; `sync` works on plain dicts so
it can be tested without a calendar.
"""

import json
import sqlite3
import sys
import time
from datetime import datetime

from config import calendar_settings

PAST_DAYS = 30
FUTURE_DAYS = 365
AUTHORIZED = (3, 4)   # EKAuthorizationStatusFullAccess / (write-only on older SDKs)


def authorization_status():
    try:
        from EventKit import EKEntityTypeEvent, EKEventStore
        return int(EKEventStore.authorizationStatusForEntityType_(EKEntityTypeEvent))
    except Exception:
        return None


def request_access(timeout: float = 60.0) -> bool:
    """Ask macOS for calendar access (shows the system prompt once). Blocks until answered."""
    from EventKit import EKEntityTypeEvent, EKEventStore
    if authorization_status() == 3:
        return True
    store = EKEventStore.alloc().init()
    result = {}

    def done(granted, error):
        result["granted"] = bool(granted)

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(done)
    else:
        store.requestAccessToEntityType_completion_(EKEntityTypeEvent, done)
    waited = 0.0
    while "granted" not in result and waited < timeout:
        time.sleep(0.2)
        waited += 0.2
    return result.get("granted", False)


def read_events(calendar_names=None) -> list[dict]:
    """Every event in the watched window, as plain dicts."""
    from EventKit import EKEntityTypeEvent, EKEventStore
    from Foundation import NSDate
    store = EKEventStore.alloc().init()
    calendars = store.calendarsForEntityType_(EKEntityTypeEvent) or []
    if calendar_names:
        wanted = {n.strip().lower() for n in calendar_names}
        calendars = [c for c in calendars if str(c.title()).lower() in wanted]
    if not calendars:
        return []
    now = time.time()
    start = NSDate.dateWithTimeIntervalSince1970_(now - PAST_DAYS * 86400)
    end = NSDate.dateWithTimeIntervalSince1970_(now + FUTURE_DAYS * 86400)
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(start, end, calendars)
    out = []
    for ev in store.eventsMatchingPredicate_(predicate) or []:
        def ts(d):
            return float(d.timeIntervalSince1970()) if d is not None else None
        out.append({
            "id": str(ev.eventIdentifier()),
            "title": str(ev.title() or "(no title)"),
            "start": ts(ev.startDate()),
            "end": ts(ev.endDate()),
            "all_day": bool(ev.isAllDay()),
            "calendar": str(ev.calendar().title()) if ev.calendar() else "",
            "location": str(ev.location()) if ev.location() else None,
            "attendees": [str(a.name() or a.URL()) for a in (ev.attendees() or [])],
            "created": ts(ev.creationDate()),
            "modified": ts(ev.lastModifiedDate()),
        })
    return out


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
    seen = {row[0]: row for row in conn.execute(
        "SELECT identifier, title, start, modified, calendar FROM calendar_seen").fetchall()}
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
                                      f"created in calendar '{ev['calendar']}'; reversibility: the event can be deleted",
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
        if authorization_status() != 3 and not request_access(timeout=1):
            return {"error": "calendar access not granted"}
        return sync(conn, settings, read_events(settings["calendars"]), user=user)
    except Exception as exc:
        print(f"[calendar] sync failed: {exc!r}; will retry next refresh", file=sys.stderr)
        return {"error": repr(exc)}


if __name__ == "__main__":
    from store import connect
    settings = calendar_settings()
    if not settings:
        print("Not configured: set RECEIPT_CALENDAR=on in .env (docs/CALENDAR.md).")
        sys.exit(1)
    print("calendar access:", "granted" if request_access() else "NOT granted (System Settings → Privacy & Security → Calendars)")
    conn = connect()
    print(sync(conn, settings, read_events(settings["calendars"])))
    conn.close()

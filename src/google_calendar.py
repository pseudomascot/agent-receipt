"""Google Calendar connector: events created, changed, or deleted, straight from Google.

Better evidence than the Mac's Calendar app: every event carries its creation
time, last-updated time, and the creator's account. Events created by an
address listed in RECEIPT_GOOGLE_AGENT_EMAILS are attributed to that agent by
credential (the agent's own Google account), the same idea as its own mailbox
or card. Everything else is attributed by evidence, like email.

Standard library only. OAuth tokens live in ~/.agent-receipt/google_token.json
(created by examples/google_calendar_auth.py, never in the repo). Incremental
sync uses Google's syncToken per calendar, kept in `meta`.
"""

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import google_calendar_settings

TOKEN_PATH = Path.home() / ".agent-receipt" / "google_token.json"
API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
PAST_DAYS = 30


class GoogleError(Exception):
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload or {}
        super().__init__(f"{status}: {(self.payload.get('error') or {}).get('message') or payload}")


# --- tokens ------------------------------------------------------------------

def load_token(path: Path = TOKEN_PATH) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_token(token: dict, path: Path = TOKEN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token, indent=1), encoding="utf-8")
    os.chmod(path, 0o600)


def _post_form(url: str, data: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except ValueError:
            payload = {}
        raise GoogleError(exc.code, payload) from None


def exchange_code(settings: dict, code: str, redirect_uri: str, post=_post_form) -> dict:
    token = post(TOKEN_URL, {"code": code, "client_id": settings["client_id"],
                             "client_secret": settings["client_secret"], "redirect_uri": redirect_uri,
                             "grant_type": "authorization_code"})
    token["obtained_at"] = time.time()
    return token


def access_token(settings: dict, token: dict, post=_post_form, path: Path = TOKEN_PATH) -> str:
    """A valid access token, refreshing (and saving) when the old one is about to expire."""
    if token.get("access_token") and time.time() < token.get("obtained_at", 0) + token.get("expires_in", 0) - 60:
        return token["access_token"]
    fresh = post(TOKEN_URL, {"refresh_token": token["refresh_token"], "client_id": settings["client_id"],
                             "client_secret": settings["client_secret"], "grant_type": "refresh_token"})
    token.update({"access_token": fresh["access_token"], "expires_in": fresh.get("expires_in", 3600),
                  "obtained_at": time.time()})
    save_token(token, path)
    return token["access_token"]


def auth_url(settings: dict, redirect_uri: str, state: str) -> str:
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": settings["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state})


# --- API ---------------------------------------------------------------------

def api_get(bearer: str, path: str, params: dict | None = None) -> dict:
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}", "User-Agent": "agent-receipt"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except ValueError:
            payload = {}
        raise GoogleError(exc.code, payload) from None


def list_changes(bearer: str, calendar_id: str, sync_token: str | None, api=api_get):
    """All events (paged) since sync_token, or a 30-day-back full listing. Returns (events, next_sync_token)."""
    events, page = [], None
    while True:
        params = {"maxResults": 2500, "showDeleted": "true", "singleEvents": "true"}
        if sync_token:
            params["syncToken"] = sync_token
        else:
            params["updatedMin"] = (datetime.now(timezone.utc) - timedelta(days=PAST_DAYS)).isoformat()
        if page:
            params["pageToken"] = page
        data = api(bearer, f"/calendars/{urllib.parse.quote(calendar_id, safe='')}/events", params)
        events.extend(data.get("items", []))
        page = data.get("nextPageToken")
        if not page:
            return events, data.get("nextSyncToken")


def _iso_to_epoch(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _when(ev: dict) -> str:
    start = ev.get("start") or {}
    if start.get("date"):
        return datetime.fromisoformat(start["date"]).strftime("%a %b %d")
    stamp = _iso_to_epoch(start.get("dateTime"))
    return datetime.fromtimestamp(stamp).strftime("%a %b %d %H:%M") if stamp else ""


INSERT = """
INSERT OR IGNORE INTO actions
    (timestamp, agent, user, source, action_type, target, amount, currency, artifact_link,
     reversible, attribution, confidence_note, raw_json, source_ref)
VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
"""


def sync(conn: sqlite3.Connection, settings: dict, calendar_id: str, events: list[dict],
         first_run: bool, user: str = "") -> dict:
    """Record the changes Google reported for one calendar."""
    counts = {"created": 0, "changed": 0, "deleted": 0, "seen": len(events)}
    now = time.time()
    agent_emails = {e.lower() for e in settings["agent_emails"]}
    for ev in events:
        eid = ev.get("id")
        if not eid:
            continue
        key = f"gcal:{calendar_id}:{eid}"
        creator = ((ev.get("creator") or {}).get("email") or "").lower()
        by_agent = creator in agent_emails
        prior = conn.execute("SELECT title, modified FROM calendar_seen WHERE identifier = ?", (key,)).fetchone()
        created = _iso_to_epoch(ev.get("created")) or now
        updated = _iso_to_epoch(ev.get("updated")) or now
        title = ev.get("summary") or "(no title)"
        label = f"{title} — {_when(ev)}" if _when(ev) else title
        link = ev.get("htmlLink")
        raw = json.dumps({"source": "google_calendar", "tool": "gcal:event", "calendar": calendar_id,
                          "event": {k: ev.get(k) for k in ("id", "summary", "start", "end", "created", "updated",
                                                            "status", "creator", "organizer", "location", "htmlLink")}})
        agent = f"google calendar ({creator})" if by_agent else settings["agent"]
        source = "log" if by_agent else "calendar"
        attribution = "agent" if by_agent else "unknown"
        cred = f"created by the agent's own Google account {creator}; " if by_agent else ""

        if ev.get("status") == "cancelled":
            if prior is not None:
                before = conn.total_changes
                conn.execute(INSERT, (updated, agent, user, source, "other", f"Deleted: {prior[0]}", link, 0, attribution,
                                      f"{cred}deleted from Google Calendar '{calendar_id}'; reversibility: a deleted event is gone",
                                      json.dumps({"source": "google_calendar", "tool": "calendar:deleted",
                                                  "event": {"id": eid, "title": prior[0]}}), f"{key}:deleted:{int(updated)}"))
                counts["deleted"] += conn.total_changes - before
                conn.execute("DELETE FROM calendar_seen WHERE identifier = ?", (key,))
            continue

        if prior is None:
            if not first_run:
                before = conn.total_changes
                conn.execute(INSERT, (created, agent, user, source, "create_event", label, link, 1, attribution,
                                      f"{cred}created in Google Calendar '{calendar_id}'; reversibility: the event can be deleted",
                                      raw, key))
                counts["created"] += conn.total_changes - before
        elif prior[1] and updated > prior[1] + 1:
            before = conn.total_changes
            conn.execute(INSERT, (updated, agent, user, source, "other", f"Changed: {label}", link, 1, attribution,
                                  f"{cred}changed in Google Calendar '{calendar_id}'; reversibility: the change can be edited back",
                                  json.dumps({"source": "google_calendar", "tool": "calendar:changed",
                                              "event": json.loads(raw)["event"], "previous_title": prior[0]}),
                                  f"{key}:{int(updated)}"))
            counts["changed"] += conn.total_changes - before
        conn.execute(
            "INSERT OR REPLACE INTO calendar_seen (identifier, title, start, modified, calendar, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, title, _iso_to_epoch((ev.get("start") or {}).get("dateTime")) or None, updated, calendar_id, now))
    conn.commit()
    return counts


def sync_calendar(conn: sqlite3.Connection, settings: dict, bearer: str, calendar_id: str,
                  user: str = "", api=api_get) -> dict:
    meta_key = f"gcal_sync_token:{calendar_id}"
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (meta_key,)).fetchone()
    sync_token = row[0] if row else None
    try:
        events, next_token = list_changes(bearer, calendar_id, sync_token, api)
    except GoogleError as exc:
        if exc.status == 410 and sync_token:            # token expired: start over, silently re-baseline
            conn.execute("DELETE FROM meta WHERE key = ?", (meta_key,))
            conn.commit()
            events, next_token = list_changes(bearer, calendar_id, None, api)
            sync_token = None
        else:
            raise
    counts = sync(conn, settings, calendar_id, events, first_run=sync_token is None, user=user)
    if next_token:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (meta_key, next_token))
        conn.commit()
    return counts


def sync_if_configured(conn: sqlite3.Connection, user: str = "") -> dict | None:
    """Called by the refresh loop. Never raises."""
    settings = google_calendar_settings()
    if not settings:
        return None
    token = load_token()
    if not token or not token.get("refresh_token"):
        return {"error": "not signed in — run examples/google_calendar_auth.py"}
    try:
        bearer = access_token(settings, token)
        totals = {"created": 0, "changed": 0, "deleted": 0, "seen": 0}
        for calendar_id in settings["calendars"]:
            for k, v in sync_calendar(conn, settings, bearer, calendar_id, user=user).items():
                totals[k] += v
        return totals
    except Exception as exc:
        print(f"[google calendar] sync failed: {exc!r}; will retry next refresh", file=sys.stderr)
        return {"error": repr(exc)}


if __name__ == "__main__":
    from store import connect
    settings = google_calendar_settings()
    if not settings:
        print("Not configured: set RECEIPT_GOOGLE_CLIENT_ID / RECEIPT_GOOGLE_CLIENT_SECRET in .env (docs/GOOGLE_CALENDAR.md).")
        sys.exit(1)
    conn = connect()
    print(sync_if_configured(conn))
    conn.close()

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import google_calendar_settings  # noqa: E402
from correlator import correlate  # noqa: E402
from google_calendar import (GoogleError, access_token, auth_url, exchange_code,  # noqa: E402
                             list_changes, sync_calendar)
from store import connect  # noqa: E402

SETTINGS = {"client_id": "cid", "client_secret": "sec", "calendars": ["primary"],
            "agent_emails": ["bot@example.com"], "agent": "google calendar"}


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts))


def _ev(i, title, created, updated=None, creator="marc@example.com", status="confirmed", start=None):
    start = start or created + 86400
    return {"id": f"g{i}", "summary": title, "status": status, "created": _iso(created), "updated": _iso(updated or created),
            "creator": {"email": creator}, "start": {"dateTime": _iso(start)}, "end": {"dateTime": _iso(start + 3600)},
            "htmlLink": f"https://calendar.google.com/event?eid=g{i}"}


class FakeAPI:
    """Pages of results keyed by syncToken; records calls."""

    def __init__(self):
        self.pages = {}       # sync_token (or None) -> list of pages (dicts)
        self.calls = []
        self.gone = set()

    def __call__(self, bearer, path, params):
        self.calls.append(params)
        token = params.get("syncToken")
        if token in self.gone:
            raise GoogleError(410, {"error": {"message": "Sync token is no longer valid"}})
        pages = self.pages[token]
        idx = int(params.get("pageToken", 0))
        page = dict(pages[idx])
        if idx + 1 < len(pages):
            page["nextPageToken"] = str(idx + 1)
        return page


def test_full_sync_then_incremental(tmp_path):
    now = time.time()
    api = FakeAPI()
    api.pages[None] = [{"items": [_ev(1, "Existing", now - 86400)]}, {"items": [_ev(2, "Also existing", now - 3600)],
                                                                      "nextSyncToken": "T1"}]
    conn = connect(tmp_path / "t.db")
    assert sync_calendar(conn, SETTINGS, "bearer", "primary", user="marc", api=api) == {
        "created": 0, "changed": 0, "deleted": 0, "seen": 2}                     # baseline: 2 pages, no rows
    assert "updatedMin" in api.calls[0] and api.calls[1]["pageToken"] == "1"
    assert conn.execute("SELECT value FROM meta WHERE key = 'gcal_sync_token:primary'").fetchone()[0] == "T1"
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0

    api.pages["T1"] = [{"items": [
        _ev(3, "Booked by bot", now - 600, creator="bot@example.com"),           # new, by the agent's account
        _ev(4, "Booked by Marc", now - 100),                                      # new, by a person's account
        _ev(2, "Also existing (moved)", now - 3600, updated=now - 50),            # changed
        dict(_ev(1, "Existing", now - 86400, updated=now - 20), status="cancelled"),   # deleted
    ], "nextSyncToken": "T2"}]
    counts = sync_calendar(conn, SETTINGS, "bearer", "primary", user="marc", api=api)
    assert counts == {"created": 2, "changed": 1, "deleted": 1, "seen": 4}
    assert api.calls[-1]["syncToken"] == "T1"
    rows = conn.execute("SELECT source, action_type, target, agent, attribution, timestamp, artifact_link, source_ref "
                        "FROM actions ORDER BY id").fetchall()
    assert rows[0][:5] == ("log", "create_event", rows[0][2], "google calendar (bot@example.com)", "agent")
    assert rows[0][2].startswith("Booked by bot — ") and rows[0][5] == float(int(now - 600)) and rows[0][7] == "gcal:primary:g3"
    assert rows[0][6] == "https://calendar.google.com/event?eid=g3"
    assert rows[1][:2] == ("calendar", "create_event") and rows[1][3] == "google calendar" and rows[1][4] == "unknown"
    assert rows[2][1:3] == ("other", "Changed: Also existing (moved) — " + time.strftime("%a %b %d %H:%M", time.localtime(now - 3600 + 86400)))
    assert rows[3][1:3] == ("other", "Deleted: Existing") and rows[3][7].startswith("gcal:primary:g1:deleted")

    # Attribution: the agent-account row stays 'agent' through the correlator; the person's row is judged by evidence.
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (now - 600, now + 60))
    conn.execute("INSERT INTO input_events (timestamp, kind) VALUES (?, 'key')", (now - 105,))
    conn.commit()
    correlate(conn)
    attributions = dict(conn.execute("SELECT source_ref, attribution FROM actions").fetchall())
    assert attributions["gcal:primary:g3"] == "agent" and attributions["gcal:primary:g4"] == "human"

    # Same delta again is a no-op; an expired token re-baselines silently.
    api.pages["T2"] = [{"items": [], "nextSyncToken": "T2"}]
    assert sync_calendar(conn, SETTINGS, "bearer", "primary", api=api)["seen"] == 0
    api.gone.add("T2")
    api.pages[None] = [{"items": [_ev(9, "Unseen before but old", now - 5000)], "nextSyncToken": "T3"}]
    assert sync_calendar(conn, SETTINGS, "bearer", "primary", api=api) == {"created": 0, "changed": 0, "deleted": 0, "seen": 1}
    assert conn.execute("SELECT value FROM meta WHERE key = 'gcal_sync_token:primary'").fetchone()[0] == "T3"
    conn.close()


def test_tokens_and_settings(tmp_path):
    posts = []

    def post(url, data):
        posts.append(data)
        return {"access_token": "at2", "expires_in": 3600, "refresh_token": "rt"} if data.get("grant_type") == "authorization_code" \
            else {"access_token": "at3", "expires_in": 3600}

    token = exchange_code(SETTINGS, "code123", "http://127.0.0.1:8766/", post=post)
    assert token["refresh_token"] == "rt" and posts[0]["grant_type"] == "authorization_code"
    path = tmp_path / "token.json"
    assert access_token(SETTINGS, token, post=post, path=path) == "at2"       # still fresh: no refresh call
    token["obtained_at"] = time.time() - 4000
    assert access_token(SETTINGS, token, post=post, path=path) == "at3"       # expired: refreshed and saved
    assert json.loads(path.read_text())["access_token"] == "at3" and posts[-1]["grant_type"] == "refresh_token"
    assert (path.stat().st_mode & 0o777) == 0o600
    url = auth_url(SETTINGS, "http://127.0.0.1:8766/", "st")
    assert "client_id=cid" in url and "calendar.events.readonly" in url and "state=st" in url

    assert google_calendar_settings({}) is None
    assert google_calendar_settings({"RECEIPT_GOOGLE_CLIENT_ID": "PASTE-CLIENT-ID", "RECEIPT_GOOGLE_CLIENT_SECRET": "x"}) is None
    s = google_calendar_settings({"RECEIPT_GOOGLE_CLIENT_ID": "a.apps", "RECEIPT_GOOGLE_CLIENT_SECRET": "b",
                                  "RECEIPT_GOOGLE_AGENT_EMAILS": "Bot@Example.com, x@y.z", "RECEIPT_GOOGLE_CALENDARS": ""})
    assert s["calendars"] == ["primary"] and s["agent_emails"] == ["Bot@Example.com", "x@y.z"]


def test_list_changes_pages_and_params():
    api = FakeAPI()
    api.pages[None] = [{"items": [{"id": "a"}]}, {"items": [{"id": "b"}], "nextSyncToken": "S"}]
    events, token = list_changes("bearer", "team@group.calendar.google.com", None, api)
    assert [e["id"] for e in events] == ["a", "b"] and token == "S"
    assert api.calls[0]["showDeleted"] == "true" and api.calls[0]["singleEvents"] == "true"

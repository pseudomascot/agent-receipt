"""The Settings page: connect a mailbox, Stripe, Ramp, Google Calendar or the Mac
Calendar from inside the app instead of editing `.env` by hand.

Everything still lives in the same private, git-ignored `.env` next to the
project, so nothing changes for people who prefer the file. Secrets are written
once and never shown again (the page only says "saved"). Saving is recorded on
the receipt by field name — never by value. Every card has a Test button that
makes one read-only call and reports in plain English.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from agents import INSERT, RECEIPT_AGENT
from config import (ENV_PATH, calendar_settings, email_settings, google_calendar_settings, load_env,
                    ramp_settings, stripe_settings)

# field: (env key, label, kind, placeholder, help)   kinds: text | secret | select:a,b | toggle
SECTIONS = [
    {
        "id": "mailbox", "title": "Agent mailbox", "doc": "docs/EMAIL.md",
        "blurb": "A mailbox that belongs to the agent, not to you. Sent mail becomes 'Emails sent'; card-issuer alert "
                 "emails routed to it become purchases. Read-only over IMAP.",
        "fields": [
            ("RECEIPT_IMAP_USER", "Mailbox address", "text", "agent.mailbox@gmail.com", ""),
            ("RECEIPT_IMAP_PASSWORD", "App password", "secret", "", "For Gmail: turn on 2-step verification, then create an app password."),
            ("RECEIPT_IMAP_HOST", "IMAP server", "text", "imap.gmail.com", "Leave as is for Gmail."),
            ("RECEIPT_EMAIL_AGENT", "Name on the receipt", "text", "Bookkeeping mailbox", "How this mailbox appears in 'Who did it'."),
        ],
        "links": [("Gmail app passwords", "https://myaccount.google.com/apppasswords")],
    },
    {
        "id": "stripe", "title": "Stripe", "doc": "docs/STRIPE.md",
        "blurb": "Charges the agent collects, and purchases on its own Issuing card where Issuing is on. A test-mode key "
                 "(sk_test_…) moves no real money.",
        "fields": [
            ("RECEIPT_STRIPE_TEST_KEY", "Secret key", "secret", "", "Dashboard → Developers → API keys. Test or live; the receipt says which."),
            ("RECEIPT_STRIPE_AGENT", "Name on the receipt", "text", "Agent card", ""),
        ],
        "links": [("Stripe API keys (test mode)", "https://dashboard.stripe.com/test/apikeys")],
    },
    {
        "id": "ramp", "title": "Ramp — a card and budget per agent", "doc": "docs/RAMP.md",
        "blurb": "Lets the Agents page hand an agent its own Ramp fund (budget + virtual card), attribute its purchases "
                 "by construction, and suspend it from the Stop page. Sandbox by default.",
        "fields": [
            ("RECEIPT_RAMP_CLIENT_ID", "Client ID", "text", "", "Ramp → Company → Developer → Apps → your app (client-credentials grant)."),
            ("RECEIPT_RAMP_CLIENT_SECRET", "Client secret", "secret", "", ""),
            ("RECEIPT_RAMP_ENV", "Environment", "select:sandbox,production", "sandbox", "Sandbox (demo.ramp.com) moves no real money."),
            ("RECEIPT_RAMP_USER_ID", "Card holder", "ramp_user", "", "The Ramp user the agents' cards are issued to. Save the credentials first, then pick."),
            ("RECEIPT_RAMP_AGENT", "Name for other Ramp purchases", "text", "", "Purchases not on an agent's fund appear under this name."),
        ],
        "links": [("Get sandbox access", "https://docs.ramp.com/developer-api/v1/sandbox-access")],
    },
    {
        "id": "google", "title": "Google Calendar", "doc": "docs/GOOGLE_CALENDAR.md",
        "blurb": "Events with real creation times. Events created by an agent's own Google account are attributed to it. "
                 "Needs a Google Cloud OAuth client (Desktop app), then a one-time sign-in as the agent.",
        "fields": [
            ("RECEIPT_GOOGLE_CLIENT_ID", "OAuth client ID", "text", "…apps.googleusercontent.com", ""),
            ("RECEIPT_GOOGLE_CLIENT_SECRET", "OAuth client secret", "secret", "", ""),
            ("RECEIPT_GOOGLE_AGENT_EMAILS", "Agent Google accounts", "text", "agent.mailbox@gmail.com", "Comma-separated. Events these accounts create count as the agent's."),
            ("RECEIPT_GOOGLE_CALENDARS", "Calendars to watch", "text", "primary", "Calendar ids, comma-separated. 'primary' is the main one."),
        ],
        "links": [("Google Cloud console", "https://console.cloud.google.com/")],
    },
    {
        "id": "calendar", "title": "Mac Calendar", "doc": "docs/CALENDAR.md",
        "blurb": "Watches the macOS Calendar app (and everything it syncs) for events created, changed or deleted. "
                 "macOS will ask once for permission the first time it runs.",
        "fields": [
            ("RECEIPT_CALENDAR", "Watch the Mac Calendar", "toggle", "off", ""),
            ("RECEIPT_CALENDARS", "Only these calendars", "text", "", "Comma-separated names; empty = all."),
        ],
        "links": [],
    },
]
SECTION_BY_ID = {s["id"]: s for s in SECTIONS}
GOOGLE_TOKEN_PATH = Path.home() / ".agent-receipt" / "google_token.json"
PLACEHOLDER_MARKERS = ("PASTE", "xxxx", "sk_test_...", "sk_live_...")


def _section_keys(section: dict) -> list[str]:
    return [f[0] for f in section["fields"]]


# --- the .env file ---------------------------------------------------------------

def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def write_env(updates: dict, path: Path | None = None) -> None:
    """Set (value) or remove (None) keys in .env, keeping every other line, comment and order.
    Written atomically with owner-only permissions."""
    path = path or ENV_PATH
    lines = _read_lines(path)
    seen = set()
    out = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in updates:
            seen.add(key)
            if updates[key] is None:
                continue                       # remove
            out.append(f"{key}={updates[key]}")
        else:
            out.append(line)
    missing = [k for k, v in updates.items() if k not in seen and v is not None]
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append("# --- set from the Settings page ---")
        out.extend(f"{k}={updates[k]}" for k in missing)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _is_placeholder(value: str) -> bool:
    return any(m in value for m in PLACEHOLDER_MARKERS)


def view(path: Path | None = None) -> dict:
    path = path or ENV_PATH
    """What the page shows: every field's current value (secrets only as 'saved'), plus status per section."""
    env = load_env(path)
    status = {
        "mailbox": email_settings(env) is not None,
        "stripe": stripe_settings(env) is not None,
        "ramp": ramp_settings(env) is not None,
        "google": google_calendar_settings(env) is not None,
        "calendar": calendar_settings(env) is not None,
    }
    sections = []
    for section in SECTIONS:
        fields = []
        for key, label, kind, placeholder, help_text in section["fields"]:
            raw = env.get(key, "")
            if _is_placeholder(raw):
                raw = ""
            fields.append({"key": key, "label": label, "kind": kind, "placeholder": placeholder, "help": help_text,
                           "value": "" if kind == "secret" else raw, "saved": bool(raw)})
        sections.append({**section, "fields": fields, "configured": status[section["id"]]})
    google_token = GOOGLE_TOKEN_PATH.exists()
    return {"sections": sections, "google_signed_in": google_token, "env_path": str(path)}


def save(section_id: str, form: dict, user: str, conn: sqlite3.Connection | None = None,
         path: Path | None = None) -> list[str]:
    """Apply one section's form. Blank secret = keep; 'clear_<KEY>' = remove. Returns the keys that changed."""
    path = path or ENV_PATH
    section = SECTION_BY_ID.get(section_id)
    if not section:
        return []
    before = load_env(path)
    updates = {}
    for key, _label, kind, _ph, _help in section["fields"]:
        if form.get(f"clear_{key}"):
            updates[key] = None
            continue
        value = form.get(key)
        value = value[-1] if isinstance(value, (list, tuple)) else value
        value = " ".join(str(value or "").split())
        if kind == "secret":
            if value:
                updates[key] = value
            continue                                    # blank keeps what is saved
        if kind == "toggle":
            updates[key] = "on" if value.lower() in ("on", "1", "true", "yes") else "off"
            continue
        updates[key] = value or None                    # blank text removes the key
    changed = [k for k, v in updates.items() if (v or "") != (before.get(k) or "")]
    if not changed:
        return []
    write_env({k: updates[k] for k in changed}, path)
    if conn is not None:
        now = time.time()
        conn.execute(INSERT, (
            now, RECEIPT_AGENT, user, "input", "other",
            f"Changed settings: {section['title']} ({', '.join(changed)})", 1, "human",
            "the Settings page was saved in Agent Receipt; reversibility: can be changed again",
            json.dumps({"source": "agent-receipt", "tool": "agent-receipt:settings", "section": section_id, "keys": changed}),
            f"agent-receipt:settings:{now:.3f}:{uuid.uuid4().hex[:8]}",
        ))
        conn.commit()
    return changed


# --- Test buttons: one read-only call each, plain-English result --------------------

def test_mailbox(env: dict) -> tuple[bool, str]:
    import imaplib
    s = email_settings(env)
    if not s:
        return False, "address and app password are not both saved yet"
    try:
        box = imaplib.IMAP4_SSL(s["host"], timeout=20)
        box.login(s["user"], s["password"])
        status, data = box.select(s["sent_folder"], readonly=True)
        sent = int(data[0]) if status == "OK" and data and data[0] else 0
        status, data = box.select(s["alerts_folder"], readonly=True)
        inbox = int(data[0]) if status == "OK" and data and data[0] else 0
        box.logout()
        return True, f"connected to {s['host']} as {s['user']}: {sent} message(s) in Sent, {inbox} in {s['alerts_folder']}"
    except Exception as exc:  # noqa: BLE001 - shown to the person
        msg = str(exc)
        if "AUTHENTICATIONFAILED" in msg.upper() or "Invalid credentials" in msg:
            return False, "the mailbox refused the login — check the address and use an app password, not the normal one"
        return False, f"could not connect: {msg[:160]}"


def test_stripe(env: dict) -> tuple[bool, str]:
    from stripe_connector import StripeError, request
    s = stripe_settings(env)
    if not s:
        return False, "no key saved yet"
    try:
        data = request(s["key"], "GET", "/v1/charges", {"limit": 3}).get("data") or []
        return True, f"connected ({s['mode']} mode): {len(data)} recent charge(s) visible"
    except StripeError as exc:
        return False, f"Stripe refused: {exc}"
    except OSError as exc:
        return False, f"could not reach Stripe: {exc}"


def test_ramp(env: dict) -> tuple[bool, str]:
    from ramp_connector import RampError, list_all, list_users
    s = ramp_settings(env)
    if not s:
        return False, "client id and secret are not both saved yet"
    try:
        users = list_users(s)
        funds = list_all(s, "/developer/v1/funds")
        holder = next((u["name"] for u in users if u["id"] == s.get("user_id")), None)
        who = f"; card holder: {holder}" if holder else "; pick a card holder below" if not s.get("user_id") else "; the saved card holder id was not found"
        return True, f"connected ({s['mode']}): {len(users)} user(s), {len(funds)} fund(s){who}"
    except RampError as exc:
        return False, f"Ramp refused: {exc}"
    except OSError as exc:
        return False, f"could not reach Ramp: {exc}"


def test_google(env: dict) -> tuple[bool, str]:
    from google_calendar import GoogleError, access_token, api_get, load_token
    s = google_calendar_settings(env)
    if not s:
        return False, "client id and secret are not both saved yet"
    token = load_token()
    if not token:
        return False, "credentials saved; not signed in yet — press “Sign in as the agent”"
    try:
        bearer = access_token(s, token)
        items = api_get(bearer, "/users/me/calendarList", {"maxResults": 10}).get("items") or []
        names = ", ".join(i.get("summary", "?") for i in items[:5])
        return True, f"signed in: {len(items)} calendar(s) visible ({names})"
    except GoogleError as exc:
        return False, f"Google refused: {exc}"
    except OSError as exc:
        return False, f"could not reach Google: {exc}"


def test_calendar(env: dict) -> tuple[bool, str]:
    if not calendar_settings(env):
        return False, "switched off"
    if sys.platform != "darwin":
        return False, "only available on macOS"
    try:
        out = subprocess.run(["osascript", "-e", 'tell application "Calendar" to count calendars'],
                             capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        return False, "the Calendar app did not answer (timed out)"
    if out.returncode != 0:
        return False, "macOS has not allowed Agent Receipt to control Calendar yet — System Settings → Privacy & Security → Automation"
    return True, f"the Calendar app answered: {out.stdout.strip()} calendar(s)"


TESTS = {"mailbox": test_mailbox, "stripe": test_stripe, "ramp": test_ramp, "google": test_google, "calendar": test_calendar}


def run_test(section_id: str, path: Path | None = None) -> tuple[bool, str]:
    path = path or ENV_PATH
    fn = TESTS.get(section_id)
    if not fn:
        return False, "no such section"
    return fn(load_env(path))


def ramp_user_choices(path: Path | None = None) -> list[dict]:
    """For the card-holder dropdown; empty if Ramp isn't connected or the call fails."""
    from ramp_connector import RampError, list_users
    path = path or ENV_PATH
    s = ramp_settings(load_env(path))
    if not s:
        return []
    try:
        users = list_users(s)
    except (RampError, OSError, ValueError, KeyError):
        return []
    return sorted([u for u in users if u.get("id")], key=lambda u: (u.get("name") or "").lower())


def start_google_signin(project_root: Path) -> str:
    """Run the one-time consent flow (it opens the browser itself)."""
    script = project_root / "examples" / "google_calendar_auth.py"
    subprocess.Popen([sys.executable, str(script)], cwd=str(project_root))
    return "a browser window is opening — sign in as the agent's Google account, then come back and press Test"

"""Email connector: what the agent's own mailbox sent, and card alerts it received.

Read-only IMAP over TLS, standard library only. Two folders:
- Sent folder -> `send_email` actions (source 'email')
- Alerts folder (usually INBOX) -> `purchase` actions for recognised issuer
  alerts (source 'card')

Position is tracked per folder by IMAP UID in `meta`, so each message is
considered once. Credentials come from .env via config.py and are never
stored. Attribution is left to the correlator: a mailbox does not say who
pressed send, so these rows come out human, agent, or unknown by evidence.
"""

import email
import email.utils
import imaplib
import json
import sqlite3
import sys
import time
from email.header import decode_header, make_header

from card_alerts import parse_card_alert
from config import email_settings

BODY_BYTES = 4000
MAX_PER_SYNC = 200


class Mailbox:
    """Thin wrapper so tests can substitute a fake."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.imap = imaplib.IMAP4_SSL(settings["host"])
        self.imap.login(settings["user"], settings["password"])

    def new_messages(self, folder: str, after_uid: int):
        name = f'"{folder}"' if " " in folder else folder
        status, _ = self.imap.select(name, readonly=True)
        if status != "OK":
            raise RuntimeError(f"cannot open folder {folder}")
        status, data = self.imap.uid("search", None, f"UID {after_uid + 1}:*")
        uids = [int(u) for u in (data[0].split() if status == "OK" and data and data[0] else [])]
        uids = [u for u in uids if u > after_uid][:MAX_PER_SYNC]
        for uid in uids:
            status, parts = self.imap.uid("fetch", str(uid), f"(BODY.PEEK[HEADER] BODY.PEEK[1]<0.{BODY_BYTES}>)")
            if status != "OK":
                continue
            literals = [p[1] for p in parts if isinstance(p, tuple) and len(p) == 2]
            if not literals:
                continue
            headers = email.message_from_bytes(literals[0])
            body = literals[1].decode("utf-8", errors="replace") if len(literals) > 1 else ""
            yield uid, headers, body

    def close(self):
        try:
            self.imap.logout()
        except Exception:
            pass


def _decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _epoch(headers) -> float:
    try:
        return email.utils.parsedate_to_datetime(headers.get("Date")).timestamp()
    except Exception:
        return time.time()


def _last_uid(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return int(row[0]) if row else 0


def _set_last_uid(conn: sqlite3.Connection, key: str, uid: int) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(uid)))


INSERT = """
INSERT OR IGNORE INTO actions
    (timestamp, agent, user, source, action_type, target, amount, currency, artifact_link,
     reversible, attribution, confidence_note, raw_json, source_ref)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'unknown', ?, ?, ?)
"""


def sync(conn: sqlite3.Connection, settings: dict, mailbox: Mailbox | None = None, user: str = "") -> dict:
    """Pull new sent mail and card alerts. Returns counts; rows are attributed later."""
    own = mailbox is None
    box = mailbox or Mailbox(settings)
    counts = {"sent": 0, "cards": 0, "skipped": 0}
    try:
        counts["sent"] = _sync_sent(conn, box, settings, user)
        counts["cards"], counts["skipped"] = _sync_alerts(conn, box, settings, user)
    finally:
        if own:
            box.close()
    conn.commit()
    return counts


def _sync_sent(conn, box, settings, user) -> int:
    key = f"email_last_uid:{settings['user']}:{settings['sent_folder']}"
    last = _last_uid(conn, key)
    inserted = 0
    for uid, headers, body in box.new_messages(settings["sent_folder"], last):
        to = ", ".join(a for _, a in email.utils.getaddresses(headers.get_all("To", []) + headers.get_all("Cc", [])))
        subject = _decode(headers.get("Subject"))
        message_id = (headers.get("Message-ID") or "").strip() or f"imap:{settings['user']}:sent:{uid}"
        before = conn.total_changes
        conn.execute(INSERT, (
            _epoch(headers), settings["agent"], user, "email", "send_email", to or "(no recipient)",
            None, None, 0,
            f"sent from mailbox {settings['user']}; reversibility: the message left the server",
            json.dumps({"subject": subject, "first_line": body.strip().splitlines()[0][:200] if body.strip() else "",
                        "uid": uid, "folder": settings["sent_folder"], "source": "email"}),
            message_id,
        ))
        inserted += conn.total_changes - before
        last = max(last, uid)
    _set_last_uid(conn, key, last)
    return inserted


def _sync_alerts(conn, box, settings, user):
    key = f"email_last_uid:{settings['user']}:{settings['alerts_folder']}"
    last = _last_uid(conn, key)
    inserted = skipped = 0
    for uid, headers, body in box.new_messages(settings["alerts_folder"], last):
        last = max(last, uid)
        subject = _decode(headers.get("Subject"))
        parsed = parse_card_alert(subject, body)
        if not parsed:
            skipped += 1
            continue
        sender = email.utils.parseaddr(headers.get("From", ""))[1]
        message_id = (headers.get("Message-ID") or "").strip() or f"imap:{settings['user']}:alert:{uid}"
        card = f", card ending {parsed['last4']}" if parsed["last4"] else ""
        before = conn.total_changes
        conn.execute(INSERT, (
            _epoch(headers), settings["agent"], user, "card", "purchase", parsed["merchant"],
            parsed["amount"], parsed["currency"], 0,
            f"card alert email from {sender}{card}; reversibility: a charge can only be disputed, not undone",
            json.dumps({"subject": subject, "uid": uid, "folder": settings["alerts_folder"],
                        "sender": sender, "last4": parsed["last4"], "source": "card"}),
            message_id,
        ))
        inserted += conn.total_changes - before
    _set_last_uid(conn, key, last)
    return inserted, skipped


def sync_if_configured(conn: sqlite3.Connection, user: str = "") -> dict | None:
    """Called by the refresh loop. Never raises: a mail problem must not stop the receipt."""
    settings = email_settings()
    if not settings:
        return None
    try:
        return sync(conn, settings, user=user)
    except Exception as exc:
        print(f"[email] sync failed: {exc!r}; will retry next refresh", file=sys.stderr)
        return {"error": repr(exc)}


if __name__ == "__main__":
    from store import connect
    settings = email_settings()
    if not settings:
        print("Not configured: copy .env.example to .env and fill in RECEIPT_IMAP_USER / RECEIPT_IMAP_PASSWORD.")
        sys.exit(1)
    conn = connect()
    print(sync(conn, settings))
    conn.close()
